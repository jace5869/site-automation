/* Site automation (1.68.1): opens a STIG scorecard link at its review in STIG Manager. nginx adds this script to STIG
   Manager's own page (docs/CKL_IMPORT.md, "Links that open the review"); the site portal serves it. The link went through
   the portal's /portal/stigman/go, which kept its target in this tab (sessionStorage "sa_stigman_open") and opened STIG
   Manager's plain address.
   Once STIG Manager is up and its person signed in, it opens, with STIG Manager's own functions:
     - with an asset (a): that asset's review of the STIG (addReview), at the rule (r) when there is one;
     - else the collection's review of the STIG (addCollectionReview), at the rule;
     - with no STIG (b), 1.69.2: the collection itself (SM.CollectionPanel.showCollectionTab), as a click on it in STIG
       Manager's tree.
   It acts only on the STIG Manager versions in its data-versions (the ones tested), only when the person can read the
   asset or the collection and the STIG is there, once, within 10 minutes of the click. Anything else - another version,
   no access, an error - it does nothing: STIG Manager's home page, as without this script, and one console line. */
(function () {
  "use strict";
  var KEY = "sa_stigman_open";
  var MAX_AGE = 10 * 60 * 1000;
  var ID = /^[0-9]{1,12}$/, BENCH = /^[A-Za-z0-9_.\- ]{1,255}$/, RULE = /^[A-Za-z0-9_.\-]{1,100}$/;
  var me = document.currentScript;
  var versions = ((me && me.getAttribute("data-versions")) || "").split(/[\s,]+/).filter(function (x) { return x });

  function say(m) { try { console.info("[site automation link] " + m) } catch (e) { /* no console */ } }
  function drop() { try { sessionStorage.removeItem(KEY) } catch (e) { /* no storage */ } }

  var raw = null;
  try { raw = sessionStorage.getItem(KEY) } catch (e) { return }
  if (!raw) return;
  var t = null;
  try { t = JSON.parse(raw) } catch (e) { drop(); return }
  if (!t || typeof t !== "object" || !(Date.now() - (Number(t.at) || 0) < MAX_AGE)) { drop(); say("the link is older than 10 minutes: not opened"); return }
  t.c = String(t.c || ""); t.b = t.b ? String(t.b) : ""; t.a = t.a ? String(t.a) : ""; t.r = t.r ? String(t.r) : "";
  if (!ID.test(t.c) || (t.b && !BENCH.test(t.b)) || (t.a && !ID.test(t.a)) || (t.r && !RULE.test(t.r)) || ((t.a || t.r) && !t.b)) {
    drop(); say("not a link this script opens"); return
  }
  var whole = !t.b;                                  // 1.69.2: the collection itself

  // The page loads twice around the sign-in (away to the sign-in page, then back): the target stays until the app is up
  var tries = 0;
  var timer = setInterval(function () {
    if (++tries > 240) { clearInterval(timer); say("STIG Manager did not finish loading in 2 minutes: not opened"); return }
    var ready = false;
    try {
      /* global Ext, STIGMAN, SM, addReview, addCollectionReview */
      ready = !!(window.Ext && Ext.getCmp && Ext.getCmp("main-tab-panel") && typeof STIGMAN !== "undefined" && STIGMAN.apiConfig &&
                 window.curUser && !document.getElementById("loading-mask") && Ext.Ajax && typeof Ext.Ajax.requestPromise === "function" &&
                 (whole ? (typeof SM !== "undefined" && SM.CollectionPanel && typeof SM.CollectionPanel.showCollectionTab === "function")
                        : (typeof addReview === "function" && typeof addCollectionReview === "function")));
    } catch (e) { ready = false }
    if (!ready) return;
    clearInterval(timer);
    drop();
    var v = "";
    try { v = String((STIGMAN.Env && STIGMAN.Env.version) || "").split("+")[0] } catch (e) { v = "" }
    if (versions.indexOf(v) < 0) { say("STIG Manager " + (v || "?") + " is not a version this was tested on (" + versions.join(", ") + "): not opened"); return }
    if (whole) openCollection(); else open();
  }, 500);

  function get(url) {
    return Ext.Ajax.requestPromise({ responseType: "json", url: url, method: "GET" });
  }

  // 1.69.2: the collection's own view - only when this person can read it (a GET of it: no grant, no answer)
  function openCollection() {
    try {
      get(STIGMAN.Env.apiBase + "/collections/" + encodeURIComponent(t.c)).then(function (x) {
        if (!x || String(x.collectionId) !== t.c || !x.name) { say("the collection was not read: not opened"); return }
        try { Ext.WindowMgr.each(function (w) { if (w && w.title === "What's New") w.close() }) } catch (e) { /* none */ }
        var path;
        try {
          var tree = (SM.Global && SM.Global.mainNavTree) || Ext.getCmp("app-nav-tree");
          var leaf = tree && typeof tree.getCollectionLeaf === "function" ? tree.getCollectionLeaf(t.c) : null;
          path = leaf && typeof leaf.getPath === "function" ? leaf.getPath() : undefined;
        } catch (e) { path = undefined }                // no tree path: the tab opens, the tree is not moved
        SM.CollectionPanel.showCollectionTab({ collectionId: t.c, collectionName: x.name, treePath: path });
        say("opened the collection " + x.name);
      }).catch(function (e) { say("not opened: " + ((e && e.message) || e)) });
    } catch (e) {
      say("not opened: " + ((e && e.message) || e));
    }
  }

  function open() {
    try {
      var api = STIGMAN.Env.apiBase;
      var tabs = Ext.getCmp("main-tab-panel");
      var key = t.b.replace(/[. ]/g, "_");
      var leaf = { collectionId: t.c, benchmarkId: t.b, revisionStr: "latest", stigName: t.b };
      // first: can this person read it, and has it that STIG? (no: nothing - no error box)
      var first = t.a ? get(api + "/assets/" + encodeURIComponent(t.a) + "?projection=stigs")
                      : get(api + "/collections/" + encodeURIComponent(t.c) + "/stigs");
      first.then(function (x) {
        var stigs = t.a ? ((x && x.stigs) || []) : (x || []);
        var has = stigs.some(function (s) { return s && (s.benchmarkId === t.b || s === t.b) });
        if (t.a && (!x || !x.collection || String(x.collection.collectionId) !== t.c)) { say("the asset is not in that collection: not opened"); return }
        if (!has) { say(t.b + " is not there: not opened"); return }
        try { Ext.WindowMgr.each(function (w) { if (w && w.title === "What's New") w.close() }) } catch (e) { /* none */ }
        // an open review ignores a new rule (addCollectionReview) or selects the old one (addReview): open it afresh
        var old = tabs.getItem(t.a ? "reviewTab-areview-" + t.a + "-" + key : "collection-review-tab-creview-" + t.c + "-" + key);
        if (old) tabs.remove(old, true);
        var done;
        if (t.a) {
          leaf.assetId = t.a;
          leaf.assetName = x.name;
          leaf.assetLabelIds = x.labelIds || [];
          done = addReview({ leaf: leaf, selectedRule: t.r || undefined });
        } else {
          done = addCollectionReview({ leaf: leaf, selectedRule: t.r || undefined });
        }
        return Promise.resolve(done).then(function () { say("opened " + (t.a ? "the asset review" : "the collection review") + " of " + t.b) });
      }).catch(function (e) { say("not opened: " + ((e && e.message) || e)) });
    } catch (e) {
      say("not opened: " + ((e && e.message) || e));
    }
  }
})();
