/* 1.141.0: Ops-center wall Teams share cards — (1) ESXi host unexpected outage, (2) Capacity (VMware datastores & NetApp volumes).
   Same dark report-card style and 4-state Copy helper (window.SAPortalCopy) as the 1.140.0 Ops monitor down-nodes card.
   Strict CSP: no innerHTML, no inline styles on page DOM, no external fetches. Missing/stale data is always grey 'not read', never green. */
(function () {
  "use strict";
  var SANS = '"Manrope","Segoe UI",Arial,sans-serif';
  var MONO = '"IBM Plex Mono",Consolas,monospace';
  var PIC_MAX_CAP = 40;
  var PIC_MAX_HOSTS = 20;
  var PIC_MAX_VMS = 64;
  var TEXT_MAX_CAP = 200;
  var SEV_BAR = {crit: "#e0454b", warn: "#e8a92c", ok: "#2f9e65", stale: "#8b95a3", off: "#8b95a3"};
  var STATE_GLYPH = {info: "\u25f7", good: "\u2713", warn: "T", bad: "!"};

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c];
    });
  }
  function safeHref(u) {
    var s = String(u || "").trim();
    return /^https?:\/\//i.test(s) ? s : "";
  }
  function h(tag, cls, kids, attrs) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (attrs) Object.keys(attrs).forEach(function (k) { if (attrs[k] != null) e.setAttribute(k, String(attrs[k])); });
    (kids || []).forEach(function (k) {
      if (k != null && k !== false) e.appendChild(typeof k === "string" ? document.createTextNode(k) : k);
    });
    return e;
  }
  function sv(tag, attrs, kids) {
    var e = document.createElementNS("http://www.w3.org/2000/svg", tag);
    if (attrs) Object.keys(attrs).forEach(function (k) { if (attrs[k] != null) e.setAttribute(k, String(attrs[k])); });
    (kids || []).forEach(function (k) { if (k) e.appendChild(k); });
    return e;
  }
  function fit(x, t, w) {
    t = String(t == null ? "" : t);
    if (w <= 8) return "";
    if (x.measureText(t).width <= w) return t;
    while (t.length > 1 && x.measureText(t + "\u2026").width > w) t = t.slice(0, -1);
    return t + "\u2026";
  }
  function rrect(x, rx, ry, rw, rh, r) {
    x.beginPath();
    if (x.roundRect) x.roundRect(rx, ry, rw, rh, r);
    else x.rect(rx, ry, rw, rh);
  }
  function numStr(n) {
    var v = Number(n);
    if (!isFinite(v)) return "";
    return Math.round(v) === v ? String(Math.round(v)) : v.toFixed(1);
  }
  function fmtHm(epochSec, tz) {
    if (epochSec == null || !isFinite(epochSec)) return "";
    try {
      return new Intl.DateTimeFormat("en-GB", {timeZone: tz || "UTC", hour: "2-digit", minute: "2-digit", hour12: false}).format(new Date(epochSec * 1000));
    } catch (e) {
      var d = new Date(epochSec * 1000);
      return String(d.getUTCHours()).padStart(2, "0") + ":" + String(d.getUTCMinutes()).padStart(2, "0");
    }
  }
  function fmtZone(epochSec, tz) {
    try {
      var parts = new Intl.DateTimeFormat("en-GB", {timeZone: tz || "UTC", timeZoneName: "short"}).formatToParts(new Date((epochSec || Date.now() / 1000) * 1000));
      for (var i = 0; i < parts.length; i++) if (parts[i].type === "timeZoneName") return parts[i].value;
    } catch (e) { /* fallback */ }
    return tz || "UTC";
  }
  function fmtDur(sec) {
    var m = Math.max(0, Math.round(sec / 60));
    if (m < 60) return m + " min";
    if (m >= 1440) {
      var dd = Math.floor(m / 1440), hh = Math.floor((m % 1440) / 60);
      return dd + " d" + (hh ? " " + hh + " h" : "");
    }
    return Math.floor(m / 60) + " h" + (m % 60 ? " " + (m % 60) + " min" : "");
  }
  function slug(s) {
    return String(s || "wall").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "wall";
  }

  // Shared 4-state clipboard helper: uses window.SAPortalCopy from portal.js with standalone fallback
  function getCopyHelp() {
    if (window.SAPortalCopy) return window.SAPortalCopy;
    var help = {
      canRichHtml: function () {
        return !!(navigator.clipboard && navigator.clipboard.write && window.ClipboardItem && window.isSecureContext
          && (!window.ClipboardItem.supports || window.ClipboardItem.supports("text/html")));
      },
      canPicture: function () {
        return !!(navigator.clipboard && navigator.clipboard.write && window.ClipboardItem && window.isSecureContext
          && (!window.ClipboardItem.supports || window.ClipboardItem.supports("image/png")));
      },
      isBlocked: function (e) { return !!(e && (e.name === "NotAllowedError" || e.name === "SecurityError")); },
      isUnsupported: function (e) { return !!(e && (e.name === "NotSupportedError" || e.name === "DataError" || e.name === "TypeError")); },
      pngDataUrl: function (cv) {
        if (!cv || typeof cv.toDataURL !== "function") return "";
        try {
          var u = cv.toDataURL("image/png");
          return typeof u === "string" && u.indexOf("data:image/png;base64,") === 0 ? u : "";
        } catch (e) { return ""; }
      },
      dataUrlToBlob: function (dataUrl) {
        if (!dataUrl || dataUrl.indexOf("data:image/png;base64,") !== 0) return null;
        try {
          var raw = atob(dataUrl.slice(22)), arr = new Uint8Array(raw.length);
          for (var i = 0; i < raw.length; i++) arr[i] = raw.charCodeAt(i);
          return new Blob([arr], {type: "image/png"});
        } catch (e) { return null; }
      },
      embedPictureHtml: function (html, dataUrl, alt, width) {
        var body = html || "", w = width || 720;
        if (!dataUrl || dataUrl.indexOf("data:image/png;base64,") !== 0) return body;
        return body + '<p><img width="' + w + '" style="max-width:100%;height:auto" alt="' + esc(alt || "Report card") + '" src="' + dataUrl + '"></p>';
      },
      copyTextFallback: function (plain) {
        var text = String(plain == null ? "" : plain);
        if (navigator.clipboard && navigator.clipboard.writeText && window.isSecureContext !== false) {
          return navigator.clipboard.writeText(text);
        }
        return Promise.reject(new Error("Clipboard access is unavailable here."));
      },
      copyMode: function (mode, opts) {
        var notify = opts.onState || function () {}, done = opts.onDone || function () {};
        var plain = String(opts.plain == null ? "" : opts.plain), html = String(opts.html || ""), dataUrl = opts.dataUrl || "";
        var failMsg = function (e) {
          if (help.isBlocked(e)) return "Clipboard access denied. Allow clipboard access and retry.";
          return (e && e.message) ? String(e.message) : "Clipboard write failed. Try copying again.";
        };
        if (mode === "text") {
          notify("info", "Copying\u2026", "Writing text\u2026");
          var pf = function () {
            return help.copyTextFallback(plain).then(function () { notify("good", "Copied", "Copied text. Ready to paste."); },
              function (e) { notify("bad", "Copy failed", failMsg(e)); }).then(done, done);
          };
          if (html && help.canRichHtml()) {
            try {
              return navigator.clipboard.write([new ClipboardItem({
                "text/plain": new Blob([plain], {type: "text/plain"}),
                "text/html": new Blob([html], {type: "text/html"})
              })]).then(function () { notify("good", "Copied", "Copied text. Ready to paste."); done(); },
                function (e) { if (help.isUnsupported(e)) return pf(); notify("bad", "Copy failed", failMsg(e)); done(); });
            } catch (e) { if (help.isUnsupported(e)) return pf(); notify("bad", "Copy failed", failMsg(e)); done(); return Promise.resolve(); }
          }
          return pf();
        }
        if (mode === "picture") {
          notify("info", "Copying\u2026", "Writing picture\u2026");
          var fb = function (why) {
            return help.copyTextFallback(plain).then(function () { notify("warn", "Text only", "Text copied. " + why); },
              function (e) { notify("bad", "Copy failed", failMsg(e)); }).then(done, done);
          };
          var blob = help.dataUrlToBlob(dataUrl);
          if (!help.canPicture() || !blob) {
            return fb(window.isSecureContext === false ? "Picture copying needs HTTPS."
              : !help.canPicture() ? "This browser does not support picture copying." : "Picture could not be drawn here.");
          }
          try {
            return navigator.clipboard.write([new ClipboardItem({"image/png": blob})]).then(function () {
              notify("good", "Copied", "Copied picture. Ready to paste."); done();
            }, function (e) {
              if (help.isUnsupported(e)) return fb("This browser does not support picture copying.");
              notify("bad", "Copy failed", failMsg(e)); done();
            });
          } catch (e) {
            if (help.isUnsupported(e)) return fb("This browser does not support picture copying.");
            notify("bad", "Copy failed", failMsg(e)); done(); return Promise.resolve();
          }
        }
        notify("info", "Copying\u2026", "Writing text and picture\u2026");
        var fbBoth = function (why) {
          return help.copyTextFallback(plain).then(function () { notify("warn", "Text only", "Text copied. " + why); },
            function (e) { notify("bad", "Copy failed", failMsg(e)); }).then(done, done);
        };
        if (!help.canRichHtml() || !dataUrl) {
          return fbBoth(window.isSecureContext === false ? "Rich HTML and picture copying needs HTTPS."
            : !help.canRichHtml() ? "This browser does not support picture copying." : "Picture could not be drawn here.");
        }
        var rich = help.embedPictureHtml(html, dataUrl, opts.alt, opts.width);
        try {
          return navigator.clipboard.write([new ClipboardItem({
            "text/plain": new Blob([plain], {type: "text/plain"}),
            "text/html": new Blob([rich], {type: "text/html"})
          })]).then(function () { notify("good", "Copied", "Copied text and picture. Ready to paste."); done(); },
            function (e) {
              if (help.isUnsupported(e)) return fbBoth("This browser does not support picture copying.");
              notify("bad", "Copy failed", failMsg(e)); done();
            });
        } catch (e) {
          if (help.isUnsupported(e)) return fbBoth("This browser does not support picture copying.");
          notify("bad", "Copy failed", failMsg(e)); done(); return Promise.resolve();
        }
      },
      setBtnState: function (btn, state, label, title) {
        if (!btn) return;
        var sp = btn.closest ? btn.closest(".copy-split") : null;
        ["st-info", "st-good", "st-warn", "st-bad"].forEach(function (k) { btn.classList.remove(k); if (sp) sp.classList.remove(k); });
        if (state) { btn.classList.add("st-" + state); if (sp) sp.classList.add("st-" + state); }
        var t = btn.querySelector(".t");
        if (label != null) { if (t) t.textContent = label; else btn.textContent = label; }
        if (title != null) btn.title = title;
      }
    };
    return help;
  }

  function wallBaseUrl(ctx) {
    if (ctx && ctx.wallUrl) return String(ctx.wallUrl);
    if (typeof location !== "undefined" && location.origin && location.pathname) {
      return location.origin + location.pathname;
    }
    return "https://portal.corp.example.mil/portal/wall";
  }

  function ctxNow(W, ctx) {
    if (ctx && typeof ctx.now === "number") return ctx.now;
    if (W && typeof W.now === "number") return W.now;
    return Date.now() / 1000;
  }

  function liveFeedFresh(W, fk, now) {
    var f = W && W.feeds && fk ? W.feeds[fk] : null;
    if (!f || f.state !== "ok" || f.asof == null) return "stale";
    var age = Math.max(0, now - Number(f.asof));
    var late = Number(f.late || (W && W.late) || 180);
    var stale = Number(f.stale || (W && W.stale) || 600);
    return age > stale ? "stale" : age > late ? "late" : "ok";
  }

  // ========================================================================= CARD 1: ESXI HOST UNEXPECTED OUTAGE
  function buildEsxi(W, ctx) {
    var raw = (W && W.cards && W.cards.esxi) || null;
    var now = ctxNow(W, ctx);
    var tz = (ctx && ctx.zone) || (W && W.zone) || "UTC";
    var site = (ctx && ctx.site) || (W && (W.enclave || W.title)) || "Ops center";
    var stamp = fmtHm(now, tz) + " " + fmtZone(now, tz);
    var base = wallBaseUrl(ctx);
    var link = base + "?area=vmware&open=" + encodeURIComponent("card:esxi");
    var sec = (W && W.seconds) || 60;
    var srcLabel = (W && W.fleet ? "vCenter + SolarWinds" : "vCenter live") + " every " + sec + " s";
    if (!raw || !raw.configured) {
      return {
        kind: "esxi", configured: false, state: "off", fresh: "stale", site: site, time: stamp, link: link,
        srcLabel: srcLabel, why: (raw && raw.why) || "vCenter not configured (not read)",
        counts: {hosts: 0, vms: 0, not_restarted: 0, restarted: 0, powering_on: 0, not_read_vms: 0, not_read_hosts: 0, excluded: 0},
        hosts: [], excluded: [], unread: [(raw && raw.why) || "vCenter not configured"]
      };
    }
    var unreach = !!(ctx && ctx.unreach);
    var vcKeys = Object.keys((W && W.feeds) || {}).filter(function (k) { return k.indexOf("vc:") === 0; });
    var rank = {ok: 0, late: 1, stale: 2};
    var worstFresh = unreach ? "stale" : (raw.fresh || "ok");
    vcKeys.forEach(function (k) {
      var fr = liveFeedFresh(W, k, now);
      if (rank[fr] > rank[worstFresh]) worstFresh = fr;
    });
    var unread = (raw.unread || []).slice();
    if (unreach) unread.unshift("portal unreachable — data not current");
    else if (worstFresh === "stale" && !unread.length) unread.push("vCenter data not current");

    var hosts = (raw.hosts || []).map(function (h0) {
      var hFresh = unreach ? "stale" : liveFeedFresh(W, h0.feed, now);
      var hStale = !!(h0.stale || hFresh === "stale");
      var sinceTxt = h0.since != null ? fmtHm(h0.since, tz) : "";
      var downFor = h0.since != null ? fmtDur(Math.max(0, now - h0.since)) : "not read";
      var vms = (h0.vms || []).map(function (v0) {
        var sev = v0.sev || "stale";
        var pSev = v0.power_sev || sev;
        var haSev = v0.ha_sev || sev;
        if (hStale) {
          if (sev === "ok") sev = "stale";
          if (pSev === "ok") pSev = "stale";
          if (haSev === "ok") haSev = "stale";
        }
        return {
          name: v0.name || "", service: v0.service || "\u2014", power: v0.power || "Not read",
          power_sev: pSev, ha: v0.ha, ha_label: v0.ha_label || (v0.ha === true ? "Yes" : v0.ha === false ? "No" : "Not read"),
          ha_target: v0.ha_target || "", ha_reason: v0.ha_reason || "", ha_sev: haSev, sev: sev
        };
      });
      var hc = {
        total: vms.length,
        not_restarted: vms.filter(function (v) { return v.ha === false; }).length,
        restarted: vms.filter(function (v) { return v.ha === true; }).length,
        powering_on: vms.filter(function (v) { return v.sev === "warn"; }).length,
        not_read: vms.filter(function (v) { return v.ha == null; }).length
      };
      return {
        name: h0.name || "", vc: h0.vc || "", cluster: h0.cluster || "", ip: h0.ip || "",
        conn: h0.conn || "", since: h0.since, sinceTxt: sinceTxt, downFor: downFor,
        feed: h0.feed || "", stale: hStale, maint_unknown: !!h0.maint_unknown,
        note: h0.note || "Unexpected outage (not in maintenance, not a planned reboot)",
        sev: hStale ? "stale" : (hc.not_restarted || hc.not_read || hc.total === 0 ? "crit" : "warn"),
        counts: hc, vms: vms
      };
    });
    var counts = {
      hosts: hosts.length,
      vms: hosts.reduce(function (a, b) { return a + b.counts.total; }, 0),
      not_restarted: hosts.reduce(function (a, b) { return a + b.counts.not_restarted; }, 0),
      restarted: hosts.reduce(function (a, b) { return a + b.counts.restarted; }, 0),
      powering_on: hosts.reduce(function (a, b) { return a + b.counts.powering_on; }, 0),
      not_read_vms: hosts.reduce(function (a, b) { return a + b.counts.not_read; }, 0),
      not_read_hosts: (raw.unread_hosts || []).length,
      excluded: (raw.excluded || []).length
    };
    var state;
    if (hosts.length) {
      if (worstFresh === "stale" && hosts.every(function (x) { return x.stale; })) state = "stale";
      else if (counts.not_restarted || counts.not_read_vms || counts.powering_on || counts.vms === 0) state = "crit";
      else state = "warn";
    } else if (worstFresh !== "ok" || unread.length || counts.not_read_hosts) {
      state = "stale";
    } else {
      state = "ok";
    }
    return {
      kind: "esxi", configured: true, state: state, fresh: worstFresh, site: site, time: stamp, link: link,
      srcLabel: srcLabel, why: unread.slice(0, 3).join(" \u00b7 "),
      counts: counts, hosts: hosts, excluded: raw.excluded || [], unread_hosts: raw.unread_hosts || [], unread: unread
    };
  }

  function esxiHeadline(d) {
    if (!d.configured || (!d.hosts.length && d.state === "stale")) {
      return d.site + " \u00b7 " + d.time + " \u00b7 ESXi host outage status not read";
    }
    if (!d.hosts.length) {
      return d.site + " \u00b7 " + d.time + " \u00b7 0 unexpected ESXi host outages";
    }
    var hl = d.site + " \u00b7 " + d.time + " \u00b7 " + d.counts.hosts + " ESXi host" + (d.counts.hosts === 1 ? "" : "s") +
      " down \u00b7 " + d.counts.vms + " VM" + (d.counts.vms === 1 ? "" : "s") + " affected";
    if (d.fresh === "stale") hl += " (not current)";
    return hl;
  }

  function esxiText(d) {
    var lines = [];
    if (!d.configured || (!d.hosts.length && d.state === "stale")) {
      lines.push(esxiHeadline(d));
      lines.push("Status: not read (grey — never shown green when data is missing or not current)");
      if (d.unread.length) lines.push("Not read: " + d.unread.slice(0, 5).join("; "));
      lines.push("");
      lines.push("Wall: " + d.link);
      return lines.join("\n");
    }
    if (!d.hosts.length) {
      lines.push(esxiHeadline(d));
      lines.push("Unexpected outages only (excludes maintenance and planned reboots)");
      if (d.counts.excluded) {
        lines.push("Excluded: " + d.counts.excluded + " host" + (d.counts.excluded === 1 ? "" : "s") + " in maintenance or planned reboot");
      }
      lines.push("");
      lines.push("Wall: " + d.link);
      return lines.join("\n");
    }
    var summary = "(" + d.counts.not_restarted + " not restarted, " + d.counts.restarted + " HA restarted" +
      (d.counts.not_read_vms ? ", " + d.counts.not_read_vms + " not read" : "") + ")";
    lines.push(esxiHeadline(d) + " " + summary);
    lines.push("Unexpected outages only (excludes maintenance and planned reboots) \u00b7 Worst first");
    if (d.unread.length) lines.push("Not read: " + d.unread.slice(0, 4).join("; "));
    lines.push("");
    d.hosts.forEach(function (host) {
      var meta = [host.ip, host.cluster].filter(Boolean).join(" \u00b7 ");
      var hc = host.counts;
      var tag = host.stale ? "[HOST DOWN - NOT CURRENT] " : "[HOST DOWN] ";
      lines.push(tag + host.name + (meta ? " (" + meta + ")" : "") + " \u2014 down " + host.downFor +
        (host.sinceTxt ? " since " + host.sinceTxt : "") + " \u00b7 " + hc.total + " VM" + (hc.total === 1 ? "" : "s") +
        " affected (" + hc.not_restarted + " not restarted, " + hc.restarted + " HA restarted" +
        (hc.not_read ? ", " + hc.not_read + " not read" : "") + ")");
      lines.push("VM | Service | Powered state | HA restarted elsewhere?");
      if (!host.vms.length) {
        lines.push("(no workload VMs recorded on this host)");
      } else {
        host.vms.forEach(function (v) {
          var haTxt = v.ha === true ? ("Yes (\u2192 " + (v.ha_target || "peer host") + ")")
            : v.ha === false ? ("No (" + (v.ha_reason || "not restarted") + ")") : "Not read";
          lines.push(v.name + " | " + v.service + " | " + v.power + " | " + haTxt);
        });
      }
      lines.push("");
    });
    lines.push("Wall: " + d.link);
    return lines.join("\n");
  }

  function esxiHtml(d) {
    var href = safeHref(d.link);
    var linkHtml = href ? '<a href="' + esc(href) + '">' + esc(d.link) + "</a>" : esc(d.link);
    var out = [];
    if (!d.configured || (!d.hosts.length && d.state === "stale")) {
      out.push('<p style="margin:0 0 6px;font-weight:700;color:#475569">' + esc(esxiHeadline(d)) + "</p>");
      out.push('<p style="margin:0 0 8px;color:#64748b"><b>Not read:</b> ' + esc(d.why || "vCenter data missing or not current") + "</p>");
      out.push('<p style="margin:8px 0 0;font-size:12px;color:#64748b">Wall: ' + linkHtml + "</p>");
      return out.join("");
    }
    if (!d.hosts.length) {
      out.push('<p style="margin:0 0 4px;font-weight:700">' + esc(esxiHeadline(d)) + "</p>");
      out.push('<p style="margin:0 0 8px;font-size:12px;color:#64748b">Unexpected outages only (excludes maintenance &amp; planned reboots)</p>');
      if (d.counts.excluded) {
        out.push('<p style="margin:0 0 8px;font-size:12px;color:#64748b">Excluded: ' + esc(d.excluded.map(function (e) { return e.name + " (" + e.reason + ")"; }).join(", ")) + "</p>");
      }
      out.push('<p style="margin:8px 0 0;font-size:12px;color:#64748b">Wall: ' + linkHtml + "</p>");
      return out.join("");
    }
    var sum = " (" + d.counts.not_restarted + " not restarted, " + d.counts.restarted + " HA restarted" +
      (d.counts.not_read_vms ? ", " + d.counts.not_read_vms + " not read" : "") + ")";
    out.push('<p style="margin:0 0 4px;font-weight:700">' + esc(esxiHeadline(d) + sum) + "</p>");
    out.push('<p style="margin:0 0 8px;font-size:12px;color:#64748b">Unexpected outages only (excludes maintenance &amp; planned reboots) &middot; Worst first</p>');
    if (d.unread.length) {
      out.push('<p style="margin:0 0 8px;padding:6px 8px;background:#f1f5f9;border-left:3px solid #64748b;font-size:12px;color:#475569"><b>Not read:</b> ' + esc(d.unread.slice(0, 4).join(" · ")) + "</p>");
    }
    d.hosts.forEach(function (host) {
      var hc = host.counts;
      var meta = [host.ip, host.cluster].filter(Boolean).join(" \u00b7 ");
      var bCol = host.stale ? "#64748b" : "#c82333";
      out.push('<div style="margin:8px 0 3px;padding:6px 8px;border-left:3px solid ' + bCol + ';background:#f8fafc;font-size:12px">');
      out.push('<span style="color:' + bCol + ';font-weight:700">' + (host.stale ? "[HOST DOWN - NOT CURRENT]" : "[HOST DOWN]") + "</span> ");
      out.push("<b>" + esc(host.name) + "</b>" + (meta ? " &middot; " + esc(meta) : ""));
      out.push(" &middot; down <span style=\"color:" + bCol + ";font-weight:700\">" + esc(host.downFor) + "</span>" + (host.sinceTxt ? " (since " + esc(host.sinceTxt) + ")" : ""));
      out.push(" &middot; " + hc.total + " VMs: <b>" + hc.not_restarted + " not restarted</b>, " + hc.restarted + " HA restarted" + (hc.not_read ? ", " + hc.not_read + " not read" : "") + "</div>");
      out.push('<table style="border-collapse:collapse;width:100%;font-size:12px;margin-bottom:8px"><thead><tr style="border-bottom:1px solid #cbd5e1;text-align:left">');
      out.push('<th style="padding:4px 6px">VM</th><th style="padding:4px 6px">Service</th><th style="padding:4px 6px">Powered state</th><th style="padding:4px 6px">HA restarted elsewhere?</th></tr></thead><tbody>');
      if (!host.vms.length) {
        out.push('<tr><td colspan="4" style="padding:4px 6px;color:#64748b">No workload VMs recorded on this host</td></tr>');
      } else {
        host.vms.forEach(function (v) {
          var stCol = v.power_sev === "crit" ? "#c82333" : v.power_sev === "warn" ? "#9a6700" : v.power_sev === "ok" ? "#1e7e4f" : "#64748b";
          var stMark = v.power_sev === "crit" ? "\u2715" : v.power_sev === "warn" ? "\u25b3" : v.power_sev === "ok" ? "\u2713" : "?";
          var haCol = v.ha_sev === "crit" ? "#c82333" : v.ha_sev === "warn" ? "#9a6700" : v.ha_sev === "ok" ? "#1e7e4f" : "#64748b";
          var haHtml = v.ha === true
            ? '<b style="color:' + haCol + '">Yes</b> &rarr; ' + esc(v.ha_target || "peer host")
            : v.ha === false
              ? '<b style="color:' + haCol + '">No</b> &middot; ' + esc(v.ha_reason || "not restarted")
              : '<b style="color:#64748b">Not read</b>';
          out.push('<tr style="border-bottom:1px solid #e2e8f0"><td style="padding:4px 6px;font-family:monospace"><b>' + esc(v.name) + '</b></td><td style="padding:4px 6px">' + esc(v.service) + '</td><td style="padding:4px 6px;color:' + stCol + ';font-weight:700">' + stMark + " " + esc(v.power) + '</td><td style="padding:4px 6px;font-family:monospace">' + haHtml + "</td></tr>");
        });
      }
      out.push("</tbody></table>");
    });
    out.push('<p style="margin:6px 0 0;font-size:12px;color:#64748b">Wall: ' + linkHtml + " &middot; " + esc(d.srcLabel) + "</p>");
    return out.join("");
  }

  function drawEsxiCard(d) {
    var cv = document.createElement("canvas"), x = cv.getContext("2d");
    var W = 1080, headH = 108, colH = 26, hostH = 44, vmH = 28, grpGap = 8, footH = 36;
    var staleAll = !d.configured || d.state === "stale" || d.fresh === "stale";
    var showHosts = d.hosts.slice(0, PIC_MAX_HOSTS);
    var vmsBudget = PIC_MAX_VMS, truncatedVms = 0;
    var plan = showHosts.map(function (host) {
      var list = host.vms.slice(0, Math.max(1, vmsBudget));
      if (!host.vms.length) return {host: host, vms: [], emptyRow: true};
      var take = Math.min(host.vms.length, Math.max(0, vmsBudget));
      vmsBudget -= take;
      truncatedVms += (host.vms.length - take);
      return {host: host, vms: host.vms.slice(0, take), extra: host.vms.length - take};
    });
    var moreHosts = Math.max(0, d.hosts.length - showHosts.length);
    var unreadBandH = d.unread.length && d.hosts.length ? 30 : 0;
    var bodyH;
    if (!d.hosts.length) {
      bodyH = 74;
    } else {
      bodyH = plan.reduce(function (acc, p) {
        var rows = p.emptyRow ? 1 : (p.vms.length + (p.extra > 0 ? 1 : 0));
        return acc + hostH + rows * vmH + grpGap;
      }, 0) + (moreHosts ? 26 : 0) + unreadBandH;
    }
    var H = headH + (d.hosts.length ? colH : 0) + bodyH + footH;
    cv.width = W * 2; cv.height = H * 2; x.scale(2, 2);

    x.fillStyle = "#141922"; x.fillRect(0, 0, W, H);
    x.fillStyle = SEV_BAR[d.state] || "#8b95a3"; x.fillRect(0, 0, 8, H);

    x.textAlign = "left"; x.textBaseline = "alphabetic";
    x.fillStyle = "#96adc2"; x.font = "600 11px " + MONO;
    x.fillText("OPS WALL \u00b7 ESXI HOST OUTAGE REPORT CARD", 28, 24);
    x.textAlign = "right";
    x.fillText(d.srcLabel, W - 24, 24);

    x.textAlign = "left";
    x.fillStyle = "#f4f7fa"; x.font = "700 22px " + SANS;
    x.fillText(fit(x, esxiHeadline(d), W - 56), 28, 54);

    var drawPill = function (px, py, txt, bg, bd, fg) {
      x.font = "700 11.5px " + MONO;
      var pw = x.measureText(txt).width + 16;
      x.fillStyle = bg; rrect(x, px, py, pw, 22, 3); x.fill();
      x.strokeStyle = bd; x.lineWidth = 1; rrect(x, px + 0.5, py + 0.5, pw - 1, 21, 3); x.stroke();
      x.fillStyle = fg; x.fillText(txt, px + 8, py + 15);
      return px + pw + 8;
    };
    var px = 28;
    if (!d.configured || (!d.hosts.length && d.state === "stale")) {
      px = drawPill(px, 68, "Not read", "#26303d", "#8b95a3", "#d4dde8");
    } else if (!d.hosts.length) {
      px = drawPill(px, 68, "0 unexpected outages", "#162d26", "#2f9e65", "#7ce0b2");
      if (d.counts.excluded) px = drawPill(px, 68, d.counts.excluded + " maint / planned excluded", "#26303d", "#6c7f96", "#cbd6e2");
    } else {
      px = drawPill(px, 68, d.counts.hosts + " host" + (d.counts.hosts === 1 ? "" : "s") + " down",
        staleAll ? "#26303d" : "#3a1c24", staleAll ? "#8b95a3" : "#e0454b", staleAll ? "#d4dde8" : "#ff9ca1");
      px = drawPill(px, 68, d.counts.not_restarted + " VMs not restarted",
        d.counts.not_restarted ? "#3a1c24" : "#26303d", d.counts.not_restarted ? "#e0454b" : "#6c7f96", d.counts.not_restarted ? "#ff9ca1" : "#cbd6e2");
      px = drawPill(px, 68, d.counts.restarted + " VMs HA restarted",
        staleAll ? "#26303d" : "#162d26", staleAll ? "#8b95a3" : "#2f9e65", staleAll ? "#d4dde8" : "#7ce0b2");
      if (d.counts.not_read_vms || d.counts.not_read_hosts || d.fresh === "stale") {
        var nr = d.counts.not_read_vms + d.counts.not_read_hosts;
        px = drawPill(px, 68, nr ? nr + " not read" : "Not current", "#26303d", "#8b95a3", "#d4dde8");
      }
    }
    x.textAlign = "right"; x.fillStyle = "#9ab0c4"; x.font = "500 11.5px " + MONO;
    x.fillText("Unexpected outages only (no maint / planned reboot) \u00b7 worst first", W - 24, 83);
    x.textAlign = "left";

    var y = headH;
    if (!d.hosts.length) {
      x.fillStyle = "#1b2330"; x.fillRect(24, y, W - 48, bodyH - 12);
      x.fillStyle = d.state === "ok" ? "#2f9e65" : "#8b95a3"; x.fillRect(24, y, 4, bodyH - 12);
      x.fillStyle = "#e6edf5"; x.font = "600 14px " + SANS;
      x.fillText(d.state === "ok"
        ? "No unexpected ESXi host outages detected in live vCenter reads."
        : "Not read \u2014 ESXi host status cannot be confirmed.", 40, y + 26);
      x.fillStyle = "#9ab0c4"; x.font = "500 12px " + MONO;
      var sub = d.state === "ok"
        ? (d.counts.excluded ? "Excluded (" + d.counts.excluded + "): " + d.excluded.map(function (e) { return e.name + " (" + e.reason + ")"; }).join(", ") : "All connected hosts healthy and current.")
        : (d.why || "vCenter read missing or not current (grey, never green)");
      x.fillText(fit(x, sub, W - 80), 40, y + 48);
    } else {
      x.fillStyle = "#202836"; x.fillRect(24, y, W - 48, colH);
      x.fillStyle = "#9eb5ca"; x.font = "700 11px " + MONO;
      x.fillText("ESXI HOST / AFFECTED VM", 36, y + 17);
      x.fillText("SERVICE", 362, y + 17);
      x.fillText("POWERED STATE", 566, y + 17);
      x.fillText("HA RESTARTED ELSEWHERE?", 756, y + 17);
      y += colH;

      plan.forEach(function (entry) {
        var host = entry.host, hc = host.counts;
        x.fillStyle = host.stale ? "#1e2530" : "#241e29"; x.fillRect(24, y, W - 48, hostH);
        x.fillStyle = host.stale ? "#8b95a3" : "#e0454b"; x.fillRect(24, y, 4, hostH);
        x.fillStyle = host.stale ? "#334155" : "#3b2d3b"; x.fillRect(24, y + hostH - 1, W - 48, 1);

        var badgeTxt = host.stale ? "NOT CURRENT" : "HOST DOWN";
        var badgeW = host.stale ? 82 : 68;
        x.fillStyle = host.stale ? "#64748b" : "#e0454b"; rrect(x, 36, y + 7, badgeW, 17, 2); x.fill();
        x.fillStyle = "#ffffff"; x.font = "700 10px " + MONO; x.textAlign = "center";
        x.fillText(badgeTxt, 36 + badgeW / 2, y + 19);
        x.textAlign = "left";

        var nameX = 36 + badgeW + 10;
        x.fillStyle = "#ffffff"; x.font = "700 13.5px " + MONO;
        x.fillText(fit(x, host.name, 285), nameX, y + 20);
        var nameW = Math.min(285, x.measureText(host.name).width);
        var meta = [host.ip, host.cluster].filter(Boolean).join(" \u00b7 ");
        if (meta) {
          x.fillStyle = "#a8bdd2"; x.font = "500 12px " + MONO;
          x.fillText(fit(x, meta, 280), nameX + nameW + 14, y + 20);
        }

        x.textAlign = "right";
        x.fillStyle = host.stale ? "#cbd6e2" : "#ff8f94"; x.font = "700 13px " + MONO;
        var durW = x.measureText(host.downFor).width;
        x.fillText(host.downFor, W - 34, y + 20);
        if (host.sinceTxt) {
          x.fillStyle = "#c4d3e1"; x.font = "500 12px " + MONO;
          x.fillText("Since " + host.sinceTxt + " \u00b7 ", W - 34 - durW, y + 20);
        }

        x.textAlign = "left";
        x.fillStyle = "#9db2c6"; x.font = "500 11.5px " + SANS;
        x.fillText(fit(x, host.note, 520), 36, y + 37);

        x.textAlign = "right";
        var rx = W - 34;
        x.font = "700 11.5px " + MONO;
        if (hc.not_read) {
          var rNr = hc.not_read + " not read";
          x.fillStyle = "#9eb0c4"; x.fillText(rNr, rx, y + 37);
          rx -= x.measureText(rNr).width;
          x.fillStyle = "#7a8fa5"; x.fillText(" \u00b7 ", rx, y + 37);
          rx -= x.measureText(" \u00b7 ").width;
        }
        var rOk = hc.restarted + " HA restarted";
        x.fillStyle = host.stale ? "#9eb0c4" : "#7ce0b2"; x.fillText(rOk, rx, y + 37);
        rx -= x.measureText(rOk).width;
        x.fillStyle = "#7a8fa5"; x.fillText(" \u00b7 ", rx, y + 37);
        rx -= x.measureText(" \u00b7 ").width;
        var rBad = hc.not_restarted + " not restarted";
        x.fillStyle = hc.not_restarted ? "#ff9297" : "#9eb0c4"; x.fillText(rBad, rx, y + 37);
        rx -= x.measureText(rBad).width;
        x.fillStyle = "#dce6f0"; x.font = "600 11.5px " + MONO;
        x.fillText(hc.total + " VM" + (hc.total === 1 ? "" : "s") + " affected \u00b7 ", rx, y + 37);
        x.textAlign = "left";
        y += hostH;

        if (entry.emptyRow) {
          x.fillStyle = "#1b2330"; x.fillRect(24, y, W - 48, vmH);
          x.fillStyle = "#8b95a3"; x.fillRect(24, y, 3, vmH);
          x.fillStyle = "#263140"; x.fillRect(24, y + vmH - 1, W - 48, 1);
          x.fillStyle = "#9ab0c4"; x.font = "italic 500 12px " + SANS;
          x.fillText("No workload VMs recorded on this host", 58, y + 18.5);
          y += vmH;
        } else {
          entry.vms.forEach(function (v, vi) {
            var isLast = (vi === entry.vms.length - 1) && !(entry.extra > 0);
            x.fillStyle = vi % 2 ? "#161d28" : "#1b2330";
            x.fillRect(24, y, W - 48, vmH);
            x.fillStyle = SEV_BAR[v.sev] || "#8b95a3";
            x.fillRect(24, y, 3, vmH);
            x.fillStyle = "#263140";
            x.fillRect(24, y + vmH - 1, W - 48, 1);

            x.fillStyle = "#3b4a5e";
            x.fillRect(42, y, 1.5, isLast ? 15 : vmH);
            x.fillRect(42, y + 14, 10, 1.5);

            var baseY = y + 18.5;
            x.fillStyle = "#f4f7fa"; x.font = "700 12.5px " + MONO;
            x.fillText(fit(x, v.name, 290), 58, baseY);

            x.fillStyle = "#dce6f0"; x.font = "500 12.5px " + SANS;
            x.fillText(fit(x, v.service, 192), 362, baseY);

            var stMark = v.power_sev === "crit" ? "\u2715" : v.power_sev === "warn" ? "\u25b3" : v.power_sev === "ok" ? "\u2713" : "?";
            var stCol = v.power_sev === "crit" ? "#ff9297" : v.power_sev === "warn" ? "#f5c76b" : v.power_sev === "ok" ? "#7ce0b2" : "#9eb0c4";
            x.fillStyle = stCol; x.font = "700 12px " + SANS;
            x.fillText(fit(x, stMark + "  " + v.power, 180), 566, baseY);

            var haWord = v.ha === true ? "Yes" : v.ha === false ? "No" : "Not read";
            var haBg = v.ha_sev === "ok" ? "#162d26" : v.ha_sev === "warn" ? "#382c16" : v.ha_sev === "crit" ? "#3a1c24" : "#26303d";
            var haBd = v.ha_sev === "ok" ? "#2f9e65" : v.ha_sev === "warn" ? "#e8a92c" : v.ha_sev === "crit" ? "#e0454b" : "#8b95a3";
            var haFg = v.ha_sev === "ok" ? "#7ce0b2" : v.ha_sev === "warn" ? "#f5c76b" : v.ha_sev === "crit" ? "#ff9297" : "#cbd6e2";
            var bw = v.ha == null ? 68 : 38;
            x.font = "700 11.5px " + MONO;
            x.fillStyle = haBg; rrect(x, 756, y + 5, bw, 18, 3); x.fill();
            x.strokeStyle = haBd; x.lineWidth = 1; rrect(x, 756.5, y + 5.5, bw - 1, 17, 3); x.stroke();
            x.fillStyle = haFg; x.textAlign = "center";
            x.fillText(haWord, 756 + bw / 2, y + 18);
            x.textAlign = "left";
            var detail = v.ha === true ? ("\u2192 " + (v.ha_target || "peer host"))
              : v.ha === false ? ("\u00b7 " + (v.ha_reason || "not restarted"))
                : (v.ha_reason ? "\u00b7 " + v.ha_reason : "");
            if (detail) {
              x.fillStyle = v.ha === true ? "#9eb5ca" : v.ha === false ? "#d9aeb3" : "#9eb0c4";
              x.font = "500 11.5px " + MONO;
              x.fillText(fit(x, detail, W - 34 - (756 + bw + 8)), 756 + bw + 8, baseY);
            }
            y += vmH;
          });
          if (entry.extra > 0) {
            x.fillStyle = "#161d28"; x.fillRect(24, y, W - 48, vmH);
            x.fillStyle = "#8b95a3"; x.fillRect(24, y, 3, vmH);
            x.fillStyle = "#9ab0c4"; x.font = "500 11.5px " + MONO;
            x.fillText("+" + entry.extra + " more affected VM" + (entry.extra === 1 ? "" : "s") + " (see full text / wall)", 58, y + 18);
            y += vmH;
          }
        }
        y += grpGap;
      });
      if (moreHosts) {
        x.fillStyle = "#9ab0c4"; x.font = "600 11.5px " + MONO;
        x.fillText("+" + moreHosts + " more down ESXi host" + (moreHosts === 1 ? "" : "s") + " not shown in picture", 36, y + 16);
        y += 26;
      }
      if (unreadBandH) {
        x.fillStyle = "#1f2633"; x.fillRect(24, y, W - 48, 24);
        x.fillStyle = "#8b95a3"; x.fillRect(24, y, 3, 24);
        x.fillStyle = "#cbd6e2"; x.font = "500 11.5px " + MONO;
        x.fillText(fit(x, "Not read: " + d.unread.slice(0, 3).join(" \u00b7 "), W - 76), 36, y + 16);
      }
    }

    x.fillStyle = "#2b3748"; x.fillRect(24, H - footH, W - 48, 1);
    x.fillStyle = "#9ab0c4"; x.font = "500 11.5px " + MONO;
    x.fillText(fit(x, "Wall: " + d.link, W - 56), 28, H - 13);
    return {cv: cv, w: W, h: H};
  }

  // ========================================================================= CARD 2: CAPACITY (VMWARE DS + NETAPP VOLS)
  function buildCap(W, ctx) {
    var raw = (W && W.cards && W.cards.capacity) || null;
    var now = ctxNow(W, ctx);
    var tz = (ctx && ctx.zone) || (W && W.zone) || "UTC";
    var site = (ctx && ctx.site) || (W && (W.enclave || W.title)) || "Ops center";
    var stamp = fmtHm(now, tz) + " " + fmtZone(now, tz);
    var base = wallBaseUrl(ctx);
    var link = base + "?area=storage&open=" + encodeURIComponent("card:capacity");
    var limits = (raw && raw.limits) || {ds: [85, 95], vol: [85, 95], trend_days: 7};
    var srcLabel = "vCenter + NetApp ONTAP \u00b7 " + (limits.trend_days || 7) + " d trend";
    if (!raw || !raw.configured) {
      return {
        kind: "capacity", configured: false, state: "off", fresh: "stale", site: site, time: stamp, link: link,
        srcLabel: srcLabel, limits: limits, sameLimits: true, why: (raw && raw.why) || "vCenter and ONTAP not configured (not read)",
        counts: {total: 0, crit: 0, warn: 0, ds: 0, vol: 0, not_read: 1, ds_total: 0, vol_total: 0},
        rows: [], unread: [(raw && raw.why) || "vCenter and ONTAP not configured"]
      };
    }
    var unreach = !!(ctx && ctx.unreach);
    var rank = {ok: 0, late: 1, stale: 2};
    var worstFresh = unreach ? "stale" : (raw.fresh || "ok");
    Object.keys((W && W.feeds) || {}).forEach(function (k) {
      if (k.indexOf("vc:") === 0 || k.indexOf("ontap:") === 0) {
        var fr = liveFeedFresh(W, k, now);
        if (rank[fr] > rank[worstFresh]) worstFresh = fr;
      }
    });
    var unread = (raw.unread || []).slice();
    if (unreach) unread.unshift("portal unreachable — data not current");
    else if (worstFresh === "stale" && !unread.length) unread.push("capacity source data not current");

    var rows = (raw.rows || []).map(function (r) {
      var rFresh = unreach ? "stale" : (r.feed ? liveFeedFresh(W, r.feed, now) : "ok");
      var rStale = !!(r.stale || rFresh === "stale");
      var dSev = r.days_sev || "none";
      if (rStale && dSev === "ok") dSev = "stale";
      return {
        kind: r.kind === "vol" ? "VOL" : "DS",
        name: r.name || "", where: r.where || "", pct: Number(r.pct),
        free: r.free_text || "not read", warn: Number(r.warn), crit: Number(r.crit),
        sev: r.sev || (Number(r.pct) >= Number(r.crit) ? "crit" : "warn"),
        days: r.days_text || "no trend", daysSev: dSev, stale: rStale
      };
    });
    var counts = {
      total: rows.length,
      crit: rows.filter(function (r) { return r.sev === "crit"; }).length,
      warn: rows.filter(function (r) { return r.sev === "warn"; }).length,
      ds: rows.filter(function (r) { return r.kind === "DS"; }).length,
      vol: rows.filter(function (r) { return r.kind === "VOL"; }).length,
      not_read: unread.length,
      ds_total: (raw.counts && raw.counts.ds_total) || 0,
      vol_total: (raw.counts && raw.counts.vol_total) || 0
    };
    var dsLim = limits.ds || [85, 95], volLim = limits.vol || [85, 95];
    var sameLimits = Number(dsLim[0]) === Number(volLim[0]) && Number(dsLim[1]) === Number(volLim[1]);
    var state;
    if (counts.crit) state = "crit";
    else if (counts.warn) state = "warn";
    else if (worstFresh !== "ok" || unread.length) state = "stale";
    else state = "ok";
    return {
      kind: "capacity", configured: true, state: state, fresh: worstFresh, site: site, time: stamp, link: link,
      srcLabel: srcLabel, limits: limits, sameLimits: sameLimits, why: unread.slice(0, 3).join(" \u00b7 "),
      sources: raw.sources || {}, counts: counts, rows: rows, unread: unread
    };
  }

  function capHeadline(d) {
    if (!d.configured || (!d.rows.length && d.state === "stale")) {
      return d.site + " \u00b7 " + d.time + " \u00b7 Capacity not read";
    }
    var hl = d.site + " \u00b7 " + d.time + " \u00b7 " + d.counts.crit + " over critical, " + d.counts.warn + " over warning";
    if (d.fresh === "stale") hl += " (not current)";
    return hl;
  }

  function capLimitsText(d) {
    var ds = d.limits.ds || [85, 95], vl = d.limits.vol || [85, 95];
    return d.sameLimits
      ? "warn " + numStr(ds[0]) + "%, crit " + numStr(ds[1]) + "%"
      : "DS warn " + numStr(ds[0]) + "%/crit " + numStr(ds[1]) + "%, VOL warn " + numStr(vl[0]) + "%/crit " + numStr(vl[1]) + "%";
  }

  function capText(d) {
    var lines = [];
    if (!d.configured || (!d.rows.length && d.state === "stale")) {
      lines.push(capHeadline(d));
      lines.push("Status: not read (grey — never shown green when data is missing or not current)");
      if (d.unread.length) lines.push("Not read: " + d.unread.slice(0, 5).join("; "));
      lines.push("");
      lines.push("Wall: " + d.link);
      return lines.join("\n");
    }
    lines.push(capHeadline(d));
    lines.push("Fullest first \u00b7 " + d.counts.total + " items (" + d.counts.ds + " datastores, " + d.counts.vol + " NetApp volumes) \u00b7 " + capLimitsText(d));
    if (d.unread.length) lines.push("Not read: " + d.unread.slice(0, 4).join("; "));
    lines.push("");
    if (!d.rows.length) {
      lines.push("All watched datastores and NetApp volumes are under configured warning limits.");
    } else {
      lines.push("Name | Where | Used % | Free | Days to full");
      var shown = d.rows.slice(0, TEXT_MAX_CAP);
      shown.forEach(function (r) {
        var tier = r.stale ? "not current" : r.sev;
        lines.push("[" + r.kind + "] " + r.name + " | " + r.where + " | " + r.pct.toFixed(1) + "% (" + tier + ", limits " + numStr(r.warn) + "/" + numStr(r.crit) + "%) | " + r.free + " | " + r.days);
      });
      if (d.rows.length > shown.length) lines.push("+" + (d.rows.length - shown.length) + " more over threshold");
    }
    lines.push("");
    lines.push("Wall: " + d.link);
    return lines.join("\n");
  }

  function capHtml(d) {
    var href = safeHref(d.link);
    var linkHtml = href ? '<a href="' + esc(href) + '">' + esc(d.link) + "</a>" : esc(d.link);
    var out = [];
    if (!d.configured || (!d.rows.length && d.state === "stale")) {
      out.push('<p style="margin:0 0 6px;font-weight:700;color:#475569">' + esc(capHeadline(d)) + "</p>");
      out.push('<p style="margin:0 0 8px;color:#64748b"><b>Not read:</b> ' + esc(d.why || "vCenter or ONTAP data missing or not current") + "</p>");
      out.push('<p style="margin:8px 0 0;font-size:12px;color:#64748b">Wall: ' + linkHtml + "</p>");
      return out.join("");
    }
    out.push('<p style="margin:0 0 4px;font-weight:700">' + esc(capHeadline(d)) + "</p>");
    out.push('<p style="margin:0 0 8px;font-size:12px;color:#64748b">Fullest first &middot; ' + d.counts.total + " items (" + d.counts.ds + " VMware datastores, " + d.counts.vol + " NetApp volumes) &middot; " + esc(capLimitsText(d)) + "</p>");
    if (d.unread.length) {
      out.push('<p style="margin:0 0 8px;padding:6px 8px;background:#f1f5f9;border-left:3px solid #64748b;font-size:12px;color:#475569"><b>Not read:</b> ' + esc(d.unread.slice(0, 4).join(" · ")) + "</p>");
    }
    if (!d.rows.length) {
      out.push('<p style="margin:0 0 8px;font-size:12px;color:#1e7e4f">All watched datastores and NetApp volumes are under warning limits.</p>');
    } else {
      out.push('<table style="border-collapse:collapse;width:100%;font-size:12px;margin-bottom:8px"><thead><tr style="border-bottom:1px solid #cbd5e1;text-align:left">');
      out.push('<th style="padding:4px 6px">Name</th><th style="padding:4px 6px">Where</th><th style="padding:4px 6px;text-align:right">Used %</th><th style="padding:4px 6px;text-align:right">Free</th><th style="padding:4px 6px;text-align:right">Days to full</th></tr></thead><tbody>');
      var prevSev = "";
      d.rows.slice(0, TEXT_MAX_CAP).forEach(function (r) {
        var sep = prevSev === "crit" && r.sev === "warn" ? "border-top:2px solid #94a3b8;" : "";
        prevSev = r.sev;
        var pCol = r.stale ? "#64748b" : r.sev === "crit" ? "#c82333" : "#9a6700";
        var dCol = r.days === "no trend" || r.days === "not read" ? "#64748b" : r.daysSev === "crit" ? "#c82333" : r.daysSev === "warn" ? "#9a6700" : "#334155";
        var chipStyle = r.kind === "DS" ? "background:#e0f2fe;color:#0369a1" : "background:#ede9fe;color:#5b21b6";
        out.push('<tr style="border-bottom:1px solid #e2e8f0;' + sep + '"><td style="padding:3px 6px;font-family:monospace;white-space:nowrap"><span style="padding:1px 4px;border-radius:3px;font-size:10px;font-weight:700;' + chipStyle + '">' + r.kind + "</span> <b>" + esc(r.name) + '</b></td><td style="padding:3px 6px;font-family:monospace">' + esc(r.where) + '</td><td style="padding:3px 6px;text-align:right;font-family:monospace;font-weight:700;color:' + pCol + '">' + r.pct.toFixed(1) + '%</td><td style="padding:3px 6px;text-align:right;font-family:monospace">' + esc(r.free) + '</td><td style="padding:3px 6px;text-align:right;font-family:monospace;color:' + dCol + '">' + esc(r.days) + "</td></tr>");
      });
      out.push("</tbody></table>");
    }
    out.push('<p style="margin:6px 0 0;font-size:12px;color:#64748b">Wall: ' + linkHtml + " &middot; " + esc(d.srcLabel) + "</p>");
    return out.join("");
  }

  function drawCapacityCard(d) {
    var cv = document.createElement("canvas"), x = cv.getContext("2d");
    var W = 1080, headH = 94, colH = 30, rowH = 27, tierGap = 4, footH = 36;
    var shown = d.rows.slice(0, PIC_MAX_CAP);
    var more = Math.max(0, d.rows.length - shown.length);
    var hasTierGap = shown.some(function (r, idx) { return idx > 0 && shown[idx - 1].sev === "crit" && r.sev === "warn"; });
    var unreadBandH = d.unread.length && shown.length ? 30 : 0;
    var bodyH = shown.length
      ? shown.length * rowH + (hasTierGap ? tierGap : 0) + (more ? 26 : 0) + unreadBandH
      : 74;
    var H = headH + (shown.length ? colH : 0) + bodyH + footH;
    cv.width = W * 2; cv.height = H * 2; x.scale(2, 2);

    x.fillStyle = "#141922"; x.fillRect(0, 0, W, H);
    x.fillStyle = d.fresh === "stale" && !d.counts.crit && !d.counts.warn ? "#8b95a3" : (SEV_BAR[d.state] || "#8b95a3");
    x.fillRect(0, 0, 8, H);

    x.textAlign = "left"; x.textBaseline = "alphabetic";
    x.fillStyle = "#96adc2"; x.font = "600 11px " + MONO;
    x.fillText("OPS WALL \u00b7 CAPACITY REPORT CARD \u00b7 VMWARE DATASTORES & NETAPP VOLUMES", 28, 24);
    x.textAlign = "right";
    x.fillText(d.srcLabel, W - 24, 24);

    x.textAlign = "left";
    x.fillStyle = "#f4f7fa"; x.font = "700 22px " + SANS;
    x.fillText(fit(x, capHeadline(d), W - 56), 28, 55);

    x.fillStyle = "#9ab0c4"; x.font = "500 11.5px " + MONO;
    var sub = shown.length
      ? "Fullest first \u00b7 " + d.counts.total + " items (" + d.counts.ds + " datastores, " + d.counts.vol + " NetApp volumes)" + (d.unread.length ? " \u00b7 " + d.unread.length + " not read" : "")
      : (d.state === "ok" ? "All watched datastores and NetApp volumes under configured limits" : "Missing or stale source \u2014 shown grey (never green)");
    x.fillText(fit(x, sub, 590), 28, 77);

    var dsL = d.limits.ds || [85, 95], volL = d.limits.vol || [85, 95];
    x.textAlign = "right"; x.font = "600 11px " + MONO;
    var rx = W - 24;
    if (d.sameLimits) {
      var cTxt = "\u2502 Crit " + numStr(dsL[1]) + "%";
      x.fillStyle = "#ff9297"; x.fillText(cTxt, rx, 77); rx -= x.measureText(cTxt).width;
      var wTxt = "\u2502 Warn " + numStr(dsL[0]) + "%   ";
      x.fillStyle = "#f5c76b"; x.fillText(wTxt, rx, 77); rx -= x.measureText(wTxt).width;
    } else {
      var vLim = "VOL " + numStr(volL[0]) + "/" + numStr(volL[1]) + "%";
      x.fillStyle = "#c4bdf8"; x.fillText(vLim, rx, 77); rx -= x.measureText(vLim).width;
      var dLim = "DS " + numStr(dsL[0]) + "/" + numStr(dsL[1]) + "%   ";
      x.fillStyle = "#8fd0f8"; x.fillText(dLim, rx, 77); rx -= x.measureText(dLim).width;
      var lTag = "\u2502 Limits: ";
      x.fillStyle = "#9ab0c4"; x.fillText(lTag, rx, 77); rx -= x.measureText(lTag).width + 8;
    }
    x.fillStyle = "#c4bdf8"; x.fillText("VOL NetApp   ", rx, 77); rx -= x.measureText("VOL NetApp   ").width;
    x.fillStyle = "#8fd0f8"; x.fillText("DS VMware   ", rx, 77);
    x.textAlign = "left";

    var y = headH;
    if (!shown.length) {
      x.fillStyle = "#1b2330"; x.fillRect(24, y, W - 48, bodyH - 12);
      x.fillStyle = d.state === "ok" ? "#2f9e65" : "#8b95a3"; x.fillRect(24, y, 4, bodyH - 12);
      x.fillStyle = "#e6edf5"; x.font = "600 14px " + SANS;
      x.fillText(d.state === "ok"
        ? "No VMware datastores or NetApp volumes over configured warning limits."
        : "Not read \u2014 capacity status cannot be confirmed.", 40, y + 26);
      x.fillStyle = "#9ab0c4"; x.font = "500 12px " + MONO;
      x.fillText(fit(x, d.state === "ok"
        ? (d.counts.ds_total + " datastores, " + d.counts.vol_total + " NetApp volumes watched \u00b7 " + capLimitsText(d))
        : (d.why || "vCenter or ONTAP read missing or not current"), W - 80), 40, y + 48);
    } else {
      var xName = 34, xWhere = 284, xPctR = 604, xBar = 616, barW = 224, xFreeR = 934, xDaysR = W - 32;
      x.fillStyle = "#202836"; x.fillRect(24, y, W - 48, colH);
      x.fillStyle = "#9eb5ca"; x.font = "700 11px " + MONO;
      x.fillText("NAME", xName, y + 19);
      x.fillText("WHERE (CLUSTER / AGGREGATE, SVM)", xWhere, y + 19);
      x.textAlign = "right"; x.fillText("USED %", xPctR, y + 19); x.textAlign = "left";
      x.fillStyle = "#788fa6"; x.font = "600 9.5px " + MONO;
      x.fillText("0%", xBar, y + 19);
      if (d.sameLimits) {
        var hwx = xBar + barW * (dsL[0] / 100), hcx = xBar + barW * (dsL[1] / 100);
        x.fillStyle = "#f5c76b"; x.textAlign = "right"; x.fillText(numStr(dsL[0]), hwx - 2, y + 19);
        x.fillStyle = "#ff9297"; x.textAlign = "center"; x.fillText(numStr(dsL[1]), Math.min(xBar + barW - 8, hcx + 2), y + 19);
      } else {
        x.fillStyle = "#9eb5ca"; x.textAlign = "right"; x.fillText("100%", xBar + barW, y + 19);
      }
      x.textAlign = "right"; x.fillStyle = "#9eb5ca"; x.font = "700 11px " + MONO;
      x.fillText("FREE", xFreeR, y + 19);
      x.fillText("DAYS TO FULL", xDaysR, y + 19);
      x.textAlign = "left";
      y += colH;

      var prevSev = "";
      shown.forEach(function (r, i) {
        var isCrit = r.sev === "crit";
        if (prevSev === "crit" && r.sev === "warn") {
          x.fillStyle = "#344256"; x.fillRect(24, y, W - 48, tierGap);
          y += tierGap;
        }
        prevSev = r.sev;

        x.fillStyle = i % 2 ? "#161d28" : "#1b2330"; x.fillRect(24, y, W - 48, rowH);
        x.fillStyle = r.stale ? "#8b95a3" : (isCrit ? "#e0454b" : "#e8a92c"); x.fillRect(24, y, 3, rowH);
        x.fillStyle = "#263140"; x.fillRect(24, y + rowH - 1, W - 48, 1);

        var baseY = y + 18;
        var isDs = r.kind === "DS";
        x.fillStyle = isDs ? "#18324a" : "#292348";
        rrect(x, xName, y + 5.5, 31, 16, 2.5); x.fill();
        x.strokeStyle = isDs ? "#386c96" : "#5b519c"; x.lineWidth = 1;
        rrect(x, xName + 0.5, y + 6, 30, 15, 2.5); x.stroke();
        x.fillStyle = isDs ? "#8fd0f8" : "#c8bcff";
        x.font = "700 9.5px " + MONO; x.textAlign = "center";
        x.fillText(r.kind, xName + 15.5, y + 16.5);
        x.textAlign = "left";

        x.fillStyle = "#f4f7fa"; x.font = "700 12px " + MONO;
        x.fillText(fit(x, r.name, xWhere - (xName + 38) - 10), xName + 38, baseY);

        x.fillStyle = "#c4d4e3"; x.font = "500 12px " + MONO;
        x.fillText(fit(x, r.where, xPctR - 64 - xWhere), xWhere, baseY);

        x.textAlign = "right";
        x.fillStyle = r.stale ? "#9eb0c4" : (isCrit ? "#ff9297" : "#f5c76b");
        x.font = "700 12.5px " + MONO;
        x.fillText(r.pct.toFixed(1) + "%", xPctR, baseY);
        x.textAlign = "left";

        // Per-row configured warn/crit limits drawn directly on the 224px bar
        var rwx = xBar + barW * (Math.min(100, Math.max(0, r.warn)) / 100);
        var rcx = xBar + barW * (Math.min(100, Math.max(0, r.crit)) / 100);
        var by = y + 7.5, bh = 12;
        x.fillStyle = "#273244"; x.fillRect(xBar, by, barW, bh);
        x.fillStyle = "rgba(232,169,44,0.22)"; x.fillRect(rwx, by, Math.max(0, rcx - rwx), bh);
        x.fillStyle = "rgba(224,69,75,0.28)"; x.fillRect(rcx, by, Math.max(0, xBar + barW - rcx), bh);

        var fillEnd = xBar + barW * (Math.min(100, Math.max(0, r.pct)) / 100);
        var fy = by + 2, fh = bh - 4;
        if (r.stale) {
          x.fillStyle = "#8b95a3"; x.fillRect(xBar, fy, Math.max(0, fillEnd - xBar), fh);
        } else {
          var seg1 = Math.min(fillEnd, rwx);
          x.fillStyle = "#7e96b0"; x.fillRect(xBar, fy, Math.max(0, seg1 - xBar), fh);
          if (fillEnd > rwx) {
            var seg2 = Math.min(fillEnd, rcx);
            x.fillStyle = "#e8a92c"; x.fillRect(rwx, fy, Math.max(0, seg2 - rwx), fh);
          }
          if (fillEnd > rcx) {
            x.fillStyle = "#e0454b"; x.fillRect(rcx, fy, Math.max(0, fillEnd - rcx), fh);
          }
        }
        // High-contrast warning and critical limit ticks
        x.fillStyle = "#141922"; x.fillRect(rwx - 1.5, by - 3, 3, bh + 6);
        x.fillStyle = "#f8cc66"; x.fillRect(rwx - 0.75, by - 3, 1.5, bh + 6);
        x.fillStyle = "#141922"; x.fillRect(rcx - 1.5, by - 3, 3, bh + 6);
        x.fillStyle = "#ff7c82"; x.fillRect(rcx - 0.75, by - 3, 1.5, bh + 6);

        x.textAlign = "right";
        x.fillStyle = "#dce6f0"; x.font = "500 12px " + MONO;
        x.fillText(r.free, xFreeR, baseY);

        if (r.days === "no trend" || r.days === "not read") {
          x.fillStyle = "#8c9fb5"; x.font = "italic 500 11.5px " + SANS;
          x.fillText(r.days, xDaysR, baseY);
        } else {
          x.fillStyle = r.daysSev === "crit" ? "#ff9297" : r.daysSev === "warn" ? "#f5c76b" : r.daysSev === "stale" ? "#9eb0c4" : "#c8d6e3";
          x.font = (r.daysSev === "crit" || r.daysSev === "warn" ? "700 " : "500 ") + "12px " + MONO;
          x.fillText(r.days, xDaysR, baseY);
        }
        x.textAlign = "left";
        y += rowH;
      });
      if (more) {
        x.fillStyle = "#9ab0c4"; x.font = "600 11.5px " + MONO;
        x.fillText("+" + more + " more over threshold (included in full text & wall)", 34, y + 16);
        y += 26;
      }
      if (unreadBandH) {
        x.fillStyle = "#1f2633"; x.fillRect(24, y, W - 48, 24);
        x.fillStyle = "#8b95a3"; x.fillRect(24, y, 3, 24);
        x.fillStyle = "#cbd6e2"; x.font = "500 11.5px " + MONO;
        x.fillText(fit(x, "Not read: " + d.unread.slice(0, 3).join(" \u00b7 "), W - 76), 36, y + 16);
      }
    }

    x.fillStyle = "#2b3748"; x.fillRect(24, H - footH, W - 48, 1);
    x.fillStyle = "#9ab0c4"; x.font = "500 11.5px " + MONO;
    x.fillText(fit(x, "Wall: " + d.link, W - 56), 28, H - 13);
    return {cv: cv, w: W, h: H};
  }

  // ========================================================================= SHARED COPY / SAVE / FEEDBACK WIRING
  function savePng(dataUrl, filename) {
    if (!dataUrl) return false;
    try {
      var a = document.createElement("a");
      a.download = filename;
      a.href = dataUrl;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      return true;
    } catch (e) { return false; }
  }

  function makeFeedback(initialMsg) {
    var glyph = h("span", "wc-fb-g", ["\u25a3"]);
    var msg = h("span", "wc-fb-m", [initialMsg]);
    var el = h("div", "wc-fb", [glyph, msg], {role: "status", "aria-live": "polite"});
    el.setState = function (st, text) {
      el.className = "wc-fb" + (st ? " " + st : "");
      glyph.textContent = STATE_GLYPH[st] || "\u25a3";
      msg.textContent = text;
    };
    return el;
  }

  function buildPayload(kind, W, ctx) {
    var d = kind === "esxi" ? buildEsxi(W, ctx) : buildCap(W, ctx);
    var plain = kind === "esxi" ? esxiText(d) : capText(d);
    var html = kind === "esxi" ? esxiHtml(d) : capHtml(d);
    var pic = kind === "esxi" ? drawEsxiCard(d) : drawCapacityCard(d);
    var copyHelp = getCopyHelp();
    var dataUrl = copyHelp.pngDataUrl(pic.cv);
    var stamp = slug(d.site) + "-" + (kind === "esxi" ? "esxi-outage" : "capacity") + "-" + d.time.slice(0, 5).replace(":", "");
    return {data: d, plain: plain, html: html, pic: pic, dataUrl: dataUrl, file: stamp + ".png"};
  }

  function makeSplitControl(opts) {
    var copyHelp = getCopyHelp();
    var kind = opts.kind;
    var label = opts.label || (kind === "esxi" ? "Share ESXi outage" : "Share capacity");
    var what = opts.what || (kind === "esxi" ? "ESXi host outage card" : "Capacity card");
    var extra = [["save", "\u2193", "Save picture", "Download 2\u00d7 PNG file"]];
    if (opts.onPreview) extra.push(["preview", "\u25a3", "Preview card\u2026", "Picture + HTML & plain text"]);

    var runAction = function (mode, btn, finish) {
      if (mode === "preview" && opts.onPreview) {
        if (finish) finish();
        opts.onPreview();
        return;
      }
      var p = opts.getPayload();
      if (mode === "save") {
        var ok = savePng(p.dataUrl, p.file);
        if (ok) {
          if (opts.fb) opts.fb.setState("good", "Saved PNG (" + p.file + "). Drag it into Teams.");
          copyHelp.setBtnState(btn, "good", "Saved", "Saved " + p.file);
        } else {
          if (opts.fb) opts.fb.setState("bad", "Picture could not be saved here.");
          copyHelp.setBtnState(btn, "bad", "Save failed", "Picture could not be saved");
        }
        if (finish) finish();
        return;
      }
      return copyHelp.copyMode(mode, {
        plain: p.plain, html: p.html, dataUrl: p.dataUrl, alt: what, width: 720, focusEl: btn,
        onState: function (st, shortLbl, msg) {
          copyHelp.setBtnState(btn, st, shortLbl, msg);
          if (opts.fb) opts.fb.setState(st, msg);
        },
        onDone: finish
      });
    };

    if (typeof copyHelp.createSplitControl === "function") {
      return copyHelp.createSplitControl({
        baseLabel: label,
        title: "Copy " + what + " as text and picture for Teams",
        cls: opts.pri ? "pri" : "sm",
        scope: "wall-card-" + kind,
        menuTitle: kind === "esxi" ? "ESXi outage card" : "Capacity card",
        menuTag: opts.tag || "Teams",
        subtitles: {both: "Formatted text + 2\u00d7 PNG", text: "HTML table + plain text", picture: "2\u00d7 HiDPI PNG card"},
        extraEntries: extra,
        onCopy: runAction
      });
    }
    // Standalone fallback button when portal.js is not present
    var b = h("button", "copy-main", [h("span", "t", [label])], {type: "button"});
    b.addEventListener("click", function () { runAction("both", b, function () {}); });
    return h("div", "copy-split", [b]);
  }

  // ========================================================================= PURE-DOM PREVIEW BUILDERS (ZERO innerHTML)
  function buildRichDom(kind, d) {
    var wrap = h("div", "wc-rich");
    if (kind === "esxi") {
      if (!d.configured || (!d.hosts.length && d.state === "stale")) {
        wrap.appendChild(h("div", "wc-rh wc-r-stale", [esxiHeadline(d)]));
        wrap.appendChild(h("div", "wc-rnote stale", ["Not read: " + (d.why || "vCenter data missing or not current")]));
        wrap.appendChild(h("div", "wc-rlink", ["Wall: " + d.link]));
        return wrap;
      }
      if (!d.hosts.length) {
        wrap.appendChild(h("div", "wc-rh", [esxiHeadline(d)]));
        wrap.appendChild(h("div", "wc-rsub", ["Unexpected outages only (excludes maintenance & planned reboots)"]));
        if (d.counts.excluded) {
          wrap.appendChild(h("div", "wc-rnote", ["Excluded (" + d.counts.excluded + "): " + d.excluded.map(function (e) { return e.name + " (" + e.reason + ")"; }).join(", ")]));
        }
        wrap.appendChild(h("div", "wc-rlink", ["Wall: " + d.link]));
        return wrap;
      }
      wrap.appendChild(h("div", "wc-rh", [esxiHeadline(d) + " (" + d.counts.not_restarted + " not restarted, " + d.counts.restarted + " HA restarted" + (d.counts.not_read_vms ? ", " + d.counts.not_read_vms + " not read" : "") + ")"]));
      wrap.appendChild(h("div", "wc-rsub", ["Unexpected outages only (excludes maintenance & planned reboots) \u00b7 Worst first"]));
      if (d.unread.length) wrap.appendChild(h("div", "wc-rnote stale", ["Not read: " + d.unread.slice(0, 4).join(" \u00b7 ")]));
      d.hosts.forEach(function (host) {
        var hc = host.counts;
        var meta = [host.ip, host.cluster].filter(Boolean).join(" \u00b7 ");
        wrap.appendChild(h("div", "wc-rband" + (host.stale ? " stale" : ""), [
          h("span", "", [h("span", host.stale ? "wc-t-stale" : "wc-t-crit", [host.stale ? "[HOST DOWN - NOT CURRENT] " : "[HOST DOWN] "]),
            h("b", "", [host.name]), meta ? " \u00b7 " + meta : "", " \u00b7 down ",
            h("span", host.stale ? "wc-t-stale" : "wc-t-crit", [host.downFor]), host.sinceTxt ? " (since " + host.sinceTxt + ")" : ""]),
          h("span", "wc-rcnt", [hc.total + " VMs: " + hc.not_restarted + " not restarted \u00b7 " + hc.restarted + " HA restarted" + (hc.not_read ? " \u00b7 " + hc.not_read + " not read" : "")])
        ]));
        var tb = h("tbody");
        if (!host.vms.length) {
          tb.appendChild(h("tr", "", [h("td", "wc-t-stale", ["No workload VMs recorded on this host"], {colspan: 4})]));
        } else {
          host.vms.forEach(function (v) {
            var stMark = v.power_sev === "crit" ? "\u2715" : v.power_sev === "warn" ? "\u25b3" : v.power_sev === "ok" ? "\u2713" : "?";
            var haNode = v.ha === true
              ? h("span", "", [h("b", "wc-t-" + v.ha_sev, ["Yes"]), " \u2192 " + (v.ha_target || "peer host")])
              : v.ha === false
                ? h("span", "", [h("b", "wc-t-" + v.ha_sev, ["No"]), " \u00b7 " + (v.ha_reason || "not restarted")])
                : h("b", "wc-t-stale", ["Not read"]);
            tb.appendChild(h("tr", "wc-rr-" + v.sev, [
              h("td", "m", [h("b", "", [v.name])]),
              h("td", "", [v.service]),
              h("td", "nw wc-t-" + v.power_sev, [stMark + " " + v.power]),
              h("td", "m nw", [haNode])
            ]));
          });
        }
        wrap.appendChild(h("table", "wc-ptab", [
          h("thead", "", [h("tr", "", [h("th", "", ["VM"]), h("th", "", ["Service"]), h("th", "", ["Powered state"]), h("th", "", ["HA restarted elsewhere?"])])]),
          tb
        ]));
      });
      wrap.appendChild(h("div", "wc-rlink", ["Wall: " + d.link + " \u00b7 " + d.srcLabel]));
      return wrap;
    }

    // Capacity rich DOM preview
    if (!d.configured || (!d.rows.length && d.state === "stale")) {
      wrap.appendChild(h("div", "wc-rh wc-r-stale", [capHeadline(d)]));
      wrap.appendChild(h("div", "wc-rnote stale", ["Not read: " + (d.why || "vCenter or ONTAP data missing or not current")]));
      wrap.appendChild(h("div", "wc-rlink", ["Wall: " + d.link]));
      return wrap;
    }
    wrap.appendChild(h("div", "wc-rh", [capHeadline(d)]));
    wrap.appendChild(h("div", "wc-rsub", ["Fullest first \u00b7 " + d.counts.total + " items (" + d.counts.ds + " VMware datastores, " + d.counts.vol + " NetApp volumes) \u00b7 " + capLimitsText(d)]));
    if (d.unread.length) wrap.appendChild(h("div", "wc-rnote stale", ["Not read: " + d.unread.slice(0, 4).join(" \u00b7 ")]));
    if (!d.rows.length) {
      wrap.appendChild(h("div", "wc-rnote", ["All watched datastores and NetApp volumes are under configured warning limits."]));
    } else {
      var ctb = h("tbody"), prev = "";
      d.rows.slice(0, TEXT_MAX_CAP).forEach(function (r) {
        var sep = prev === "crit" && r.sev === "warn" ? " tier-sep" : "";
        prev = r.sev;
        var pCls = r.stale ? "wc-t-stale" : r.sev === "crit" ? "wc-t-crit" : "wc-t-warn";
        var dCls = r.days === "no trend" || r.days === "not read" ? "wc-t-stale" : r.daysSev === "crit" ? "wc-t-crit" : r.daysSev === "warn" ? "wc-t-warn" : "";
        ctb.appendChild(h("tr", "wc-rr-" + (r.stale ? "stale" : r.sev) + sep, [
          h("td", "m nw", [h("span", "wc-chip " + (r.kind === "DS" ? "ds" : "vol"), [r.kind]), " ", h("b", "", [r.name])]),
          h("td", "m", [r.where]),
          h("td", "m r " + pCls, [r.pct.toFixed(1) + "%"]),
          h("td", "m r", [r.free]),
          h("td", "m r " + dCls, [r.days])
        ]));
      });
      wrap.appendChild(h("table", "wc-ptab compact", [
        h("thead", "", [h("tr", "", [h("th", "", ["Name"]), h("th", "", ["Where (cluster / aggregate, SVM)"]), h("th", "r", ["Used %"]), h("th", "r", ["Free"]), h("th", "r", ["Days to full"])])]),
        ctb
      ]));
    }
    wrap.appendChild(h("div", "wc-rlink", ["Wall: " + d.link + " \u00b7 " + d.srcLabel]));
    return wrap;
  }

  // ========================================================================= ON-PAGE CONTEXT PANELS (NEVER GREEN ON MISSING)
  function miniBarSvg(r) {
    var svg = sv("svg", {viewBox: "0 0 100 12", preserveAspectRatio: "none", "class": "wc-mbar", role: "img",
      "aria-label": r.name + " " + r.pct.toFixed(1) + "% used, warning " + numStr(r.warn) + "%, critical " + numStr(r.crit) + "%"});
    svg.appendChild(sv("rect", {x: 0, y: 2, width: 100, height: 8, rx: 1.5, "class": "wc-mb-trk"}));
    svg.appendChild(sv("rect", {x: r.warn, y: 2, width: Math.max(0, r.crit - r.warn), height: 8, "class": "wc-mb-zw"}));
    svg.appendChild(sv("rect", {x: r.crit, y: 2, width: Math.max(0, 100 - r.crit), height: 8, "class": "wc-mb-zc"}));
    var w = Math.max(1, Math.min(100, r.pct));
    var fCls = r.stale ? "wc-mb-stale" : (r.sev === "crit" ? "wc-mb-crit" : "wc-mb-warn");
    svg.appendChild(sv("rect", {x: 0, y: 3, width: w, height: 6, rx: 1, "class": fCls}));
    svg.appendChild(sv("line", {x1: r.warn, x2: r.warn, y1: 0, y2: 12, "class": "wc-mb-tw"}));
    svg.appendChild(sv("line", {x1: r.crit, x2: r.crit, y1: 0, y2: 12, "class": "wc-mb-tc"}));
    return svg;
  }

  function ctxFromApi(api) {
    var W = api.W();
    return {
      now: api.NOW() / 1000, zone: W.zone || "UTC", site: W.enclave || W.title || "Ops center",
      unreach: api.unreach ? api.unreach() : false,
      wallUrl: location.origin + (api.wallPath || location.pathname)
    };
  }

  function esxiPanel(api) {
    var W = api.W(), ctx = ctxFromApi(api);
    var d = buildEsxi(W, ctx);
    var badgeSt = d.state === "crit" ? "crit" : d.state === "warn" ? "warn" : "stale";
    var badgeTxt = d.hosts.length ? (d.fresh === "stale" ? "Not current" : d.counts.hosts + " host" + (d.counts.hosts === 1 ? "" : "s") + " down")
      : (d.state === "ok" ? "0 unexpected outages" : "Not read");
    var summary = d.hosts.length
      ? d.counts.vms + " VMs affected \u00b7 " + d.counts.not_restarted + " not restarted \u00b7 " + d.counts.restarted + " HA restarted" + (d.counts.not_read_vms ? " \u00b7 " + d.counts.not_read_vms + " not read" : "")
      : d.state === "ok"
        ? "No unexpected down ESXi hosts (maintenance & planned reboots excluded)" + (d.counts.excluded ? " \u00b7 " + d.counts.excluded + " excluded" : "")
        : "Not read: " + (d.why || "vCenter data missing or not current");
    var fb = makeFeedback("Ready to copy ESXi outage report card (text + 2\u00d7 PNG) for Teams.");
    var split = makeSplitControl({
      kind: "esxi", label: "Share ESXi outage", what: "ESXi host outage card", pri: d.hosts.length > 0,
      tag: d.hosts.length ? (d.counts.hosts + " host" + (d.counts.hosts === 1 ? "" : "s") + " \u00b7 " + d.counts.vms + " VMs") : (d.state === "ok" ? "0 down" : "not read"),
      fb: fb,
      getPayload: function () { return buildPayload("esxi", api.W(), ctxFromApi(api)); },
      onPreview: function () { api.openDetail("card:esxi"); }
    });
    var prevBtn = h("button", "wc-prevbtn", ["Preview card"], {type: "button", title: "Open the ESXi outage Teams card preview (picture + HTML + plain text)", "data-d": "card:esxi"});
    var head = h("div", "wc-head", [
      h("div", "wc-hl", [
        h("span", "wc-badge wc-b-" + (d.state === "ok" ? "neutral" : badgeSt), [badgeTxt]),
        h("span", "wc-title", ["ESXi host unexpected outage card"]),
        h("span", "wc-sub", [summary])
      ]),
      h("div", "wc-actions", [split, prevBtn])
    ]);
    var bodyKids = [];
    if (d.hosts.length) {
      d.hosts.slice(0, 3).forEach(function (host) {
        var hc = host.counts;
        bodyKids.push(h("div", "wc-hrow" + (host.stale ? " stale" : ""), [
          h("span", "wc-hnm", [host.name]),
          h("span", "wc-hmeta", [[host.ip, host.cluster].filter(Boolean).join(" \u00b7 ")]),
          h("span", "wc-hdur", ["down " + host.downFor + (host.sinceTxt ? " (since " + host.sinceTxt + ")" : "")]),
          h("span", "wc-hcnt", [hc.total + " VMs \u00b7 " + hc.not_restarted + " not restarted \u00b7 " + hc.restarted + " HA restarted" + (hc.not_read ? " \u00b7 " + hc.not_read + " not read" : "")])
        ], {"data-d": "card:esxi", tabindex: "0", role: "button"}));
      });
      if (d.hosts.length > 3) {
        bodyKids.push(h("div", "wc-more", ["+" + (d.hosts.length - 3) + " more down ESXi hosts included in the Teams card"]));
      }
    } else if (d.unread.length) {
      bodyKids.push(h("div", "wc-more stale", ["Not read: " + d.unread.slice(0, 3).join(" \u00b7 ")]));
    }
    return h("section", "wc-panel wc-p-" + (d.state === "ok" ? "neutral" : badgeSt), [head, bodyKids.length ? h("div", "wc-list", bodyKids) : null, fb], {"data-wall-card": "esxi"});
  }

  function capPanel(api) {
    var W = api.W(), ctx = ctxFromApi(api);
    var d = buildCap(W, ctx);
    var badgeSt = d.state === "crit" ? "crit" : d.state === "warn" ? "warn" : "stale";
    var badgeTxt = d.rows.length
      ? d.counts.crit + " crit \u00b7 " + d.counts.warn + " warn"
      : (d.state === "ok" ? "0 over limits" : "Not read");
    var summary = d.rows.length
      ? "Fullest first \u00b7 " + d.counts.total + " items (" + d.counts.ds + " datastores, " + d.counts.vol + " NetApp volumes) \u00b7 " + capLimitsText(d)
      : d.state === "ok"
        ? d.counts.ds_total + " datastores, " + d.counts.vol_total + " NetApp volumes under limits (" + capLimitsText(d) + ")"
        : "Not read: " + (d.why || "vCenter or ONTAP data missing or not current");
    var fb = makeFeedback("Ready to copy Capacity report card (" + (d.rows.length ? d.rows.length + " rows" : d.state === "ok" ? "0 over limits" : "not read") + ", text + 2\u00d7 PNG) for Teams.");
    var split = makeSplitControl({
      kind: "capacity", label: "Share capacity", what: "Capacity card (" + d.counts.total + " rows)", pri: d.counts.crit > 0,
      tag: d.rows.length ? (d.counts.total + " rows") : (d.state === "ok" ? "0 over" : "not read"),
      fb: fb,
      getPayload: function () { return buildPayload("capacity", api.W(), ctxFromApi(api)); },
      onPreview: function () { api.openDetail("card:capacity"); }
    });
    var prevBtn = h("button", "wc-prevbtn", ["Preview card"], {type: "button", title: "Open the Capacity Teams card preview (picture + HTML + plain text)", "data-d": "card:capacity"});
    var head = h("div", "wc-head", [
      h("div", "wc-hl", [
        h("span", "wc-badge wc-b-" + (d.state === "ok" ? "neutral" : badgeSt), [badgeTxt]),
        h("span", "wc-title", ["Capacity card \u00b7 VMware datastores & NetApp volumes"]),
        h("span", "wc-sub", [summary])
      ]),
      h("div", "wc-actions", [split, prevBtn])
    ]);
    var bodyKids = [];
    if (d.rows.length) {
      d.rows.slice(0, 3).forEach(function (r) {
        bodyKids.push(h("div", "wc-crow " + (r.stale ? "stale" : r.sev), [
          h("span", "wc-chip " + (r.kind === "DS" ? "ds" : "vol"), [r.kind]),
          h("span", "wc-cnm", [r.name], {title: r.name}),
          h("span", "wc-cwh", [r.where], {title: r.where}),
          miniBarSvg(r),
          h("span", "wc-cnum " + (r.stale ? "stale" : r.sev), [r.pct.toFixed(1) + "%"]),
          h("span", "wc-cfree", [r.free]),
          h("span", "wc-cdays " + (r.daysSev === "crit" ? "crit" : r.daysSev === "warn" ? "warn" : "muted"), [r.days])
        ], {"data-d": "card:capacity", tabindex: "0", role: "button"}));
      });
      var rest = d.rows.length - 3;
      bodyKids.push(h("div", "wc-more", [
        h("span", "", [rest > 0 ? ("Showing top 3 on wall panel \u00b7 +" + rest + " more over threshold included in the Teams card") : ("Showing all " + d.rows.length + " items over threshold")]),
        h("span", "", [capLimitsText(d)])
      ]));
    }
    if (d.unread.length) {
      bodyKids.push(h("div", "wc-more stale", ["Not read: " + d.unread.slice(0, 3).join(" \u00b7 ")]));
    }
    return h("section", "wc-panel wc-p-" + (d.state === "ok" ? "neutral" : badgeSt), [head, bodyKids.length ? h("div", "wc-list", bodyKids) : null, fb], {"data-wall-card": "capacity"});
  }

  // ========================================================================= FULL CARD PREVIEW MODAL (FOR #dsCard / ?open=card:*)
  function renderModal(cardEl, kind, api, onClose) {
    var payload = buildPayload(kind, api.W(), ctxFromApi(api));
    var d = payload.data;
    while (cardEl.firstChild) cardEl.removeChild(cardEl.firstChild);

    var title = kind === "esxi" ? "ESXi host unexpected outage — Teams card" : "Capacity (VMware datastores & NetApp volumes) — Teams card";
    var badgeSt = d.state === "crit" ? "crit" : d.state === "warn" ? "warn" : d.state === "ok" ? "neutral" : "stale";
    var badgeLbl = d.state === "crit" ? "Critical" : d.state === "warn" ? "Warning" : d.state === "ok" ? "Current" : "Not read";
    var closeBtn = h("button", "x", ["\u00d7"], {type: "button", "aria-label": "Close"});
    closeBtn.addEventListener("click", function (ev) { ev.stopPropagation(); if (onClose) onClose(); });
    var head = h("div", "dhead wc-mhead", [
      h("div", "dh1", [
        h("div", "dht", [h("h2", "", [title]), h("span", "wc-badge wc-b-" + badgeSt, [badgeLbl])]),
        h("div", "chips", [h("span", "", [d.site]), h("span", "", [d.time]), h("span", "", [d.srcLabel])])
      ]),
      closeBtn
    ]);

    var fb = makeFeedback("Click Copy to write text and picture together for Teams, or choose text / picture / Save PNG.");
    var split = makeSplitControl({
      kind: kind, label: kind === "esxi" ? "Copy ESXi outage card" : "Copy capacity card",
      what: kind === "esxi" ? "ESXi host outage card" : "Capacity card (" + d.counts.total + " rows)",
      pri: true, tag: "Teams", fb: fb,
      getPayload: function () { return payload; }
    });
    var saveBtn = h("button", "wc-prevbtn", ["Save PNG"], {type: "button", title: "Download 2\u00d7 PNG picture"});
    saveBtn.addEventListener("click", function () {
      var ok = savePng(payload.dataUrl, payload.file);
      fb.setState(ok ? "good" : "bad", ok ? "Saved PNG (" + payload.file + "). Drag it into Teams." : "Picture could not be saved here.");
    });
    var bar = h("div", "wc-mbar-top", [split, saveBtn, fb]);

    // Section 1: Pasted picture (2x PNG)
    var img = h("img", "wc-mimg", [], {alt: title, src: payload.dataUrl});
    var picPane = h("div", "wc-mpane", [
      h("div", "wc-mpane-h", [h("span", "", ["Pasted picture (2\u00d7 PNG)"]), h("span", "m", ["PNG \u00b7 " + payload.pic.w + "\u00d7" + payload.pic.h + " @ 2\u00d7"])]),
      h("div", "wc-mimg-wrap", [img])
    ]);

    // Section 2: Pasted text (Formatted HTML DOM vs Plain text toggle)
    var richEl = buildRichDom(kind, d);
    var plainEl = h("pre", "wc-plain is-hidden", [payload.plain]);
    var btnRich = h("button", "wc-seg on", ["Formatted (HTML)"], {type: "button", "aria-pressed": "true"});
    var btnPlain = h("button", "wc-seg", ["Plain text"], {type: "button", "aria-pressed": "false"});
    btnRich.addEventListener("click", function () {
      btnRich.classList.add("on"); btnRich.setAttribute("aria-pressed", "true");
      btnPlain.classList.remove("on"); btnPlain.setAttribute("aria-pressed", "false");
      richEl.classList.remove("is-hidden"); plainEl.classList.add("is-hidden");
    });
    btnPlain.addEventListener("click", function () {
      btnPlain.classList.add("on"); btnPlain.setAttribute("aria-pressed", "true");
      btnRich.classList.remove("on"); btnRich.setAttribute("aria-pressed", "false");
      plainEl.classList.remove("is-hidden"); richEl.classList.add("is-hidden");
    });
    var txtPane = h("div", "wc-mpane", [
      h("div", "wc-mpane-h", [h("span", "", ["Pasted text"]), h("div", "wc-segs", [btnRich, btnPlain])]),
      h("div", "wc-mtxt-wrap", [richEl, plainEl])
    ]);

    var body = h("div", "wc-mbody share", [bar, picPane, txtPane]);
    cardEl.appendChild(head);
    cardEl.appendChild(body);
    return payload;
  }

  window.SAWallCards = {
    version: "1.141.0",
    esxi: {build: buildEsxi, headline: esxiHeadline, text: esxiText, html: esxiHtml, picture: drawEsxiCard, panel: esxiPanel},
    capacity: {build: buildCap, headline: capHeadline, text: capText, html: capHtml, picture: drawCapacityCard, panel: capPanel},
    buildPayload: buildPayload,
    renderModal: renderModal
  };
})();
