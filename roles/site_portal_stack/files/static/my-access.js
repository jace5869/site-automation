/* 1.139.0: Read-only My access page (/portal/access)
   Matches mock/my-access.html in all 10 portal looks, light & dark, 800 & 1280 px.
   Live data only from /portal/api/access (GET only); never shows anything the user may not see.
   Strict CSP: pure DOM creation only (no HTML string injection or inline style attributes). */
(function () {
  "use strict";
  var ROOT = document.getElementById("my-access");
  if (!ROOT) return;
  var SRC = ROOT.getAttribute("data-src") || "/portal/api/access";

  var S = {
    data: null, err: "", selRole: "", selCtrl: 0,
    reqRole: "Runner", reqNote: "", fetchedAt: 0
  };

  function h(tag, cls, kids, attrs) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (attrs) Object.keys(attrs).forEach(function (k) { if (attrs[k] !== null && attrs[k] !== undefined) e.setAttribute(k, String(attrs[k])); });
    (kids || []).forEach(function (c) {
      if (c === null || c === undefined || c === false) return;
      e.appendChild(typeof c === "string" || typeof c === "number" ? document.createTextNode(String(c)) : c);
    });
    return e;
  }
  function clear(el) { while (el && el.firstChild) el.removeChild(el.firstChild); }

  function statusWord(st) {
    return st === "yes" ? "Granted" : st === "scoped" ? "Scoped" : st === "unk" ? "Not known" : "Not granted";
  }
  function capWord(st) {
    return st === "yes" ? "✓ Allowed" : st === "scoped" ? "◐ Scoped" : st === "unk" ? "? Not known" : "— No";
  }
  function fmtAge(sec) {
    if (sec === null || sec === undefined || isNaN(sec)) return "not read";
    sec = Math.max(0, Math.floor(sec));
    if (sec < 60) return sec + "s ago";
    if (sec < 3600) return Math.floor(sec / 60) + "m " + (sec % 60) + "s ago";
    return Math.floor(sec / 3600) + "h " + Math.floor((sec % 3600) / 60) + "m ago";
  }

  function requestText(doc) {
    var id = doc.identity || {};
    var hi = doc.highest || {};
    var lines = [
      "Subject: Site portal access request — " + S.reqRole,
      "",
      "Hello " + (doc.contact || "portal administrator") + ",",
      "",
      "Please grant me " + S.reqRole + " access on the Site portal.",
      "Card CN: " + (id.cn || "unknown"),
      "Current highest role: " + (hi.title || "Viewer") + " (" + statusWord(hi.status) + ")"
    ];
    if (S.reqNote && S.reqNote.trim()) lines.push("Templates / reason: " + S.reqNote.trim());
    lines.push("", "Generated from read-only My access (/portal/access).");
    return lines.join("\n");
  }

  function copyText(txt, msgEl) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(txt).then(function () {
        if (msgEl) msgEl.textContent = "Request text copied to clipboard.";
      }, function () {
        if (msgEl) msgEl.textContent = "Clipboard blocked - select and copy the box manually.";
      });
    } else if (msgEl) {
      msgEl.textContent = "Clipboard unavailable - select and copy manually.";
    }
  }

  function updateFreshnessBar() {
    if (!S.data || !S.data.ad) return;
    var ad = S.data.ad;
    var bar = ROOT.querySelector(".ma-fresh");
    if (!bar) return;
    if (!ad.configured) return;
    var age = ad.age_s !== null && ad.age_s !== undefined ? (ad.age_s + (Date.now() - S.fetchedAt) / 1000) : null;
    var late = ad.late_s || 300, stale = ad.stale_s || 600;
    var st = !ad.read ? "not_read" : age === null ? "not_read" : age > stale ? "stale" : age > late ? "late" : "ok";
    bar.className = "ma-fresh " + st;
    var lbl = bar.querySelector(".ma-fresh-age");
    if (lbl) lbl.textContent = ad.read ? ("Last AD read " + fmtAge(age) + (st === "stale" ? " — EXPIRED" : st === "late" ? " — LATE" : " — current")) : ("Not read — " + (ad.why || "no AD read yet"));
    var fl = bar.querySelector(".ma-fresh-fill");
    if (fl) fl.style.width = (age === null ? 100 : Math.min(100, Math.max(3, (age / Math.max(1, stale * 1.1)) * 100))).toFixed(1) + "%";
  }

  function render() {
    clear(ROOT);
    if (S.err) {
      ROOT.appendChild(h("div", "ma-ban bad", [h("span", "tag", ["ERROR"]), h("span", "", [S.err])]));
      return;
    }
    if (!S.data) {
      ROOT.appendChild(h("p", "note", ["Reading your portal access…"]));
      return;
    }
    var doc = S.data;
    var id = doc.identity || {};
    var ad = doc.ad || {};
    var roles = doc.roles || [];
    if (!S.selRole && doc.highest) S.selRole = doc.highest.id;

    // 1. Header + Read-only badge
    ROOT.appendChild(h("div", "ma-head", [
      h("div", "ma-head-l", [
        h("h1", "", ["My access"]),
        h("p", "", ["What your card and Active Directory groups allow you to do on this portal — live from the portal's own access rules."])
      ]),
      h("span", "ma-ro", ["Read-only · No changes made here"])
    ]));

    // 2. Identity strip
    var rulesTxt = (id.card_rules || []).map(function (r) { return r.attribute + " = " + r.match; }).join(", ") || "None configured";
    ROOT.appendChild(h("div", "ma-id", [
      h("div", "ma-id-cell", [
        h("span", "k", ["Verified card CN"]),
        h("span", "v", [id.cn || "—"]),
        h("span", "s", [id.short ? ("Signed in as " + id.short + (id.limited ? " (limited scope)" : "")) : ""])
      ]),
      h("div", "ma-id-cell", [
        h("span", "k", ["AD card match"]),
        h("span", "v", [ad.card_matched === true ? "Matched AD account" : ad.card_matched === false ? "No AD account matched" : "Not known / AD not read"]),
        h("span", "s", [(id.matched_attributes && id.matched_attributes.length) ? ("Matched via " + id.matched_attributes.join(", ")) : ("Rules: " + rulesTxt)])
      ]),
      h("div", "ma-id-cell", [
        h("span", "k", ["Two-person approval rule"]),
        h("span", "v", [doc.self_approval ? "Self-approval allowed" : "Peer approval required"]),
        h("span", "s", [doc.self_approval ? "portal_allow_self_approval is true" : "portal_allow_self_approval is false"])
      ])
    ]));

    // 3. AD cache freshness bar
    var fBox = h("div", "ma-fresh " + (ad.state || "not_read"));
    if (!ad.configured) {
      fBox.appendChild(h("div", "ma-fresh-top", [
        h("b", "", ["Active Directory group cache"]),
        h("span", "ma-mono", ["No AD groups configured — card lists & open defaults only"])
      ]));
    } else {
      var latePct = Math.min(92, ((ad.late_s || 300) / Math.max(1, (ad.stale_s || 600) * 1.1)) * 100);
      var stalePct = Math.min(96, ((ad.stale_s || 600) / Math.max(1, (ad.stale_s || 600) * 1.1)) * 100);
      var mkL = h("i", "ma-fresh-mk"); mkL.style.left = latePct.toFixed(1) + "%";
      var mkS = h("i", "ma-fresh-mk"); mkS.style.left = stalePct.toFixed(1) + "%";
      var fl = h("div", "ma-fresh-fill");
      fBox.appendChild(h("div", "ma-fresh-top", [
        h("b", "", ["Active Directory group cache"]),
        h("span", "ma-mono ma-fresh-age", [""])
      ]));
      fBox.appendChild(h("div", "ma-fresh-trk", [fl, mkL, mkS]));
      fBox.appendChild(h("div", "ma-fresh-leg", [
        h("span", "", ["0s"]),
        h("span", "", ["Refresh: " + (ad.seconds || 300) + "s · Stale: " + (ad.stale_s || 600) + "s"]),
        h("span", "", [ad.degraded ? "Degraded (named lists only)" : "Current"])
      ]));
    }
    ROOT.appendChild(fBox);
    updateFreshnessBar();

    // 4. Degraded AD or access.json error banners
    if (ad.degraded) {
      ROOT.appendChild(h("div", "ma-ban", [
        h("span", "tag", ["AD NOT CURRENT"]),
        h("span", "", ["Active Directory group membership is not current (" + (ad.why || "AD not read") + "). Only explicitly named card lists apply; AD-backed roles show Not known, never granted."])
      ]));
    }
    if (doc.access_file_error) {
      ROOT.appendChild(h("div", "ma-ban bad", [
        h("span", "tag", ["ACCESS LIST"]),
        h("span", "", ["Saved template access file (/data/access.json) reported an error: " + doc.access_file_error])
      ]));
    }

    // 5. Hero highest role + interactive 4-step Role Ladder
    var hero = h("section", "ma-hero");
    var hi = doc.highest || {title: "Viewer", status: "no", kind: "", via: ""};
    hero.appendChild(h("div", "ma-hero-top", [
      h("div", "ma-hero-role", [
        h("span", "ey", ["Highest active role"]),
        h("strong", "", [hi.title]),
        h("span", "ma-pill " + hi.status, [statusWord(hi.status)])
      ]),
      h("span", "ma-hero-via", [(hi.kind ? hi.kind + " · " : "") + (hi.via || "")])
    ]));

    var lad = h("div", "ma-ladder");
    roles.forEach(function (r) {
      var stBtn = h("button", "ma-step " + r.status + (S.selRole === r.id ? " sel" : ""), [
        h("div", "ma-step-h", [h("b", "", [r.title]), h("span", "ma-pill " + r.status, [statusWord(r.status)])]),
        h("div", "via", [r.kind + " · " + r.via]),
        h("div", "det", [r.detail])
      ], {type: "button"});
      stBtn.addEventListener("click", function () { S.selRole = r.id; render(); });
      lad.appendChild(stBtn);
    });
    hero.appendChild(lad);
    ROOT.appendChild(hero);

    // 6. Exact role grant trace table
    var traceCard = h("section", "ma-card");
    traceCard.appendChild(h("div", "ma-ch", [
      h("h2", "", ["Exact role grant trace"]),
      h("span", "cnt", ["How each portal role was evaluated for your card"])
    ]));
    var tBody = h("tbody", "", roles.map(function (r) {
      return h("tr", S.selRole === r.id ? "hi" : "", [
        h("td", "", [h("b", "", [r.title])]),
        h("td", "", [h("span", "ma-pill " + r.status, [statusWord(r.status)])]),
        h("td", "ma-mono", [r.kind]),
        h("td", "ma-mono", [r.via]),
        h("td", "ma-mono", [r.settings]),
        h("td", "", [r.detail])
      ]);
    }));
    traceCard.appendChild(h("div", "ma-tw", [h("table", "ma-tbl", [
      h("thead", "", [h("tr", "", [h("th", "", ["Role"]), h("th", "", ["Status"]), h("th", "", ["Source"]),
        h("th", "", ["Matched rule / group"]), h("th", "", ["Settings checked"]), h("th", "", ["Explanation"])])]),
      tBody
    ])]));
    ROOT.appendChild(traceCard);

    // 7. Two-column row: Per-template overrides + What the portal read from AD
    var two = h("div", "ma-two");

    var tplCard = h("section", "ma-card");
    var tpls = doc.templates || [];
    tplCard.appendChild(h("div", "ma-ch", [
      h("h2", "", ["Per-template access overrides"]),
      h("span", "cnt", [tpls.length + " visible template" + (tpls.length === 1 ? "" : "s")])
    ]));
    if (!tpls.length) {
      tplCard.appendChild(h("div", "ma-empty", ["No per-template overrides or action who lists apply to the templates you can view."]));
    } else {
      tplCard.appendChild(h("div", "ma-tw", [h("table", "ma-tbl", [
        h("thead", "", [h("tr", "", [h("th", "", ["Template"]), h("th", "", ["Run"]), h("th", "", ["Approve"]), h("th", "", ["Rule / notes"])])]),
        h("tbody", "", tpls.map(function (t) {
          return h("tr", "", [
            h("td", "", [h("b", "", [t.title]), h("div", "ma-mono", [t.area + " · " + t.kind + (t.confirm ? " · extra confirm" : "") + (t.stale ? " · stale" : "")])]),
            h("td", "", [h("span", "ma-pill " + (t.can_run ? "yes" : "no"), [t.can_run ? "Yes" : "No"])]),
            h("td", "", [h("span", "ma-pill " + (t.can_approve ? "yes" : "no"), [t.approval ? (t.can_approve ? "Yes" : "No") : "N/A"])]),
            h("td", "", [t.via + (t.why && t.why !== "Ready" ? (" — " + t.why) : "")])
          ]);
        }))
      ])]));
    }
    two.appendChild(tplCard);

    var adCard = h("section", "ma-card");
    var groups = ad.groups || [];
    adCard.appendChild(h("div", "ma-ch", [
      h("h2", "", ["What the portal read from AD"]),
      h("span", "cnt", [ad.read ? "Read OK" : "Not read"])
    ]));
    if (!ad.configured) {
      adCard.appendChild(h("div", "ma-empty", ["No AD groups are configured on this portal."]));
    } else {
      var gRows = groups.length ? groups.map(function (g) {
        return h("tr", g.matched ? "hi" : "", [
          h("td", "ma-mono", [g.role]),
          h("td", "ma-mono", [g.name]),
          h("td", "", [h("span", "ma-pill " + (g.found === true ? "yes" : g.found === false ? "bad" : "unk"),
            [g.found === true ? "Found" : g.found === false ? "Missing" : "Not known"])]),
          h("td", "", [h("span", "ma-pill " + (g.matched ? "yes" : "no"), [g.matched ? "Member" : "Not member"])])
        ]);
      }) : [h("tr", "", [h("td", "", [ad.why || "No AD groups read yet."], {colspan: 4})])];
      adCard.appendChild(h("div", "ma-tw", [h("table", "ma-tbl", [
        h("thead", "", [h("tr", "", [h("th", "", ["Role"]), h("th", "", ["Group"]), h("th", "", ["In AD"]), h("th", "", ["Your card"])])]),
        h("tbody", "", gRows)
      ])]));
    }
    two.appendChild(adCard);
    ROOT.appendChild(two);

    // 8. Capability matrix (active user role columns highlighted)
    var capCard = h("section", "ma-card");
    capCard.appendChild(h("div", "ma-ch", [
      h("h2", "", ["Capability matrix"]),
      h("span", "cnt", ["Your active role columns are highlighted"])
    ]));
    var activeRole = {};
    roles.forEach(function (r) { activeRole[r.id] = (r.status === "yes" || r.status === "scoped"); });
    capCard.appendChild(h("div", "ma-tw", [h("table", "ma-tbl", [
      h("thead", "", [h("tr", "", [
        h("th", "", ["Capability"]),
        h("th", activeRole.viewer ? "mycol" : "", ["Viewer"]),
        h("th", activeRole.runner ? "mycol" : "", ["Runner"]),
        h("th", activeRole.approver ? "mycol" : "", ["Approver"]),
        h("th", activeRole.admin ? "mycol" : "", ["Admin"]),
        h("th", "", ["Your access"])
      ])]),
      h("tbody", "", (doc.capabilities || []).map(function (c) {
        return h("tr", "", [
          h("td", "", [c.cap]),
          h("td", (activeRole.viewer ? "mycol " : "") + "ma-mono", [capWord(c.viewer)]),
          h("td", (activeRole.runner ? "mycol " : "") + "ma-mono", [capWord(c.runner)]),
          h("td", (activeRole.approver ? "mycol " : "") + "ma-mono", [capWord(c.approver)]),
          h("td", (activeRole.admin ? "mycol " : "") + "ma-mono", [capWord(c.admin)]),
          h("td", "", [h("span", "ma-pill " + c.mine, [capWord(c.mine)])])
        ]);
      }))
    ])]));
    ROOT.appendChild(capCard);

    // 9. Interactive "Why is something greyed out?" diagnostic inspector
    var diagCard = h("section", "ma-card");
    var ctrls = doc.controls || [];
    diagCard.appendChild(h("div", "ma-ch", [
      h("h2", "", ["Why is something greyed out?"]),
      h("span", "cnt", [ctrls.length + " live control check" + (ctrls.length === 1 ? "" : "s")])
    ]));
    if (!ctrls.length) {
      diagCard.appendChild(h("div", "ma-empty", ["No blocked controls detected for your current role."]));
    } else {
      if (S.selCtrl >= ctrls.length) S.selCtrl = 0;
      var cur = ctrls[S.selCtrl];
      var dList = h("div", "ma-diag-list");
      ctrls.forEach(function (c, i) {
        var pCls = c.state === "allowed" ? "yes" : c.state === "aap_off" ? "warn" : c.state === "unknown" ? "unk" : "no";
        var pTxt = c.state === "allowed" ? "Allowed" : c.state === "aap_off" ? "AAP off" : c.state === "unknown" ? "Not known" : "Blocked";
        var b = h("button", "ma-diag-btn" + (i === S.selCtrl ? " sel" : ""), [
          h("span", "", [c.control]),
          h("span", "ma-pill " + pCls, [pTxt])
        ], {type: "button"});
        b.addEventListener("click", function () { S.selCtrl = i; render(); });
        dList.appendChild(b);
      });
      var dPane = h("div", "ma-diag-pane", [
        h("h3", "", [cur.control]),
        h("div", "ma-mono", ["Page: " + (cur.page || "Portal")]),
        h("div", "", [h("b", "", ["Why: "]), cur.reason || "—"]),
        h("div", "", [h("b", "", ["What to do: "]), cur.fix || "—"])
      ]);
      diagCard.appendChild(h("div", "ma-diag", [dList, dPane]));
    }
    ROOT.appendChild(diagCard);

    // 10. Who to ask + copyable access request builder
    var askCard = h("section", "ma-card");
    askCard.appendChild(h("div", "ma-ch", [
      h("h2", "", ["Who to ask for additional access"]),
      h("span", "cnt", ["Contact: " + (doc.contact || "your portal administrator")])
    ]));
    var roleSel = h("select", "", ["Viewer", "Runner", "Approver", "Admin"].map(function (r) {
      var o = h("option", "", [r], {value: r});
      if (r === S.reqRole) o.selected = true;
      return o;
    }));
    var noteIn = h("input", "", [], {type: "text", placeholder: "Optional: template name or operational reason", value: S.reqNote});
    var pre = h("pre", "ma-pre", [requestText(doc)]);
    var msg = h("span", "ma-msg");
    roleSel.addEventListener("change", function () { S.reqRole = roleSel.value; pre.textContent = requestText(doc); });
    noteIn.addEventListener("input", function () { S.reqNote = noteIn.value; pre.textContent = requestText(doc); });
    var cpBtn = h("button", "ma-btn pri", ["Copy request text"], {type: "button"});
    cpBtn.addEventListener("click", function () { copyText(pre.textContent, msg); });

    var form = h("div", "ma-form", [
      h("p", "", ["Send the message below to ", h("b", "", [doc.contact || "your portal administrator"]), ". This page never submits changes."]),
      h("label", "", ["Requested role", roleSel]),
      h("label", "", ["Optional template or note", noteIn])
    ]);
    askCard.appendChild(h("div", "ma-req", [
      form,
      h("div", "", [pre, h("div", "ma-acts", [cpBtn, msg])])
    ]));
    ROOT.appendChild(askCard);
  }

  function load() {
    fetch(SRC, {headers: {Accept: "application/json"}, credentials: "same-origin", cache: "no-store"}).then(function (res) {
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.json();
    }).then(function (doc) {
      S.data = doc;
      S.err = "";
      S.fetchedAt = Date.now();
      render();
    }).catch(function (err) {
      S.err = "Could not read your access details: " + (err && err.message ? err.message : "network error");
      render();
    });
  }

  document.addEventListener("portal-look", function () { if (S.data) render(); });
  setInterval(updateFreshnessBar, 1000);
  load();
})();
