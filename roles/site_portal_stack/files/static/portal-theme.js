// The colours chosen last time (Auto, Light or Dark), set before the page is drawn - one choice for the portal and the report
// website (the same key as the report pages: site-report-theme). Its radio buttons work by themselves (CSS); this keeps the choice.
(function () {
  "use strict";
  var root = document.documentElement;
  try {
    var t = localStorage.getItem("site-report-theme");
    if (t === "light" || t === "dark") root.setAttribute("data-theme", t);
  } catch (e) { /* no storage: Auto */ }
  // 1.136.1: the look (portal_look; html[data-skin], static/portal-looks.css). ?look=KEY shows that look on this page only (as the wall's
  // ?style=); a pick in the bar's Look list is kept in this browser (site-portal-look). Removed or unknown names fall back to portal_look silently.
  var LOOKS = ["metro-atlas", "metro-calm", "metro-wall", "atlas-clean", "atlas-night", "contour-lite", "strata", "metro", "relay", "atlas"];
  var lk = null, picked = null;
  try { lk = new URLSearchParams(location.search).get("look"); } catch (e) { lk = null; }
  try { picked = localStorage.getItem("site-portal-look"); } catch (e) { picked = null; }
  if (LOOKS.indexOf(lk) >= 0) root.setAttribute("data-skin", lk);
  else if (LOOKS.indexOf(picked) >= 0) root.setAttribute("data-skin", picked);
  // 1.87.0: motion, decided before the page is drawn (so nothing shows and then jumps to its start): portal_motion always (html.motion-always) moves,
  // off (html.motion-off) never, auto moves unless the system asks for less - and follows that setting when it changes. portal.css: html.moves.
  var cl = root.classList;
  var mq = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
  var motion = function () { cl.toggle("moves", cl.contains("motion-always") || (!cl.contains("motion-off") && !(mq && mq.matches))); };
  motion();
  if (mq && mq.addEventListener) mq.addEventListener("change", motion);
  document.addEventListener("DOMContentLoaded", function () {
    var lp = document.getElementById("look");
    if (lp) {
      lp.value = root.getAttribute("data-skin") || lp.value;
      lp.addEventListener("change", function () {
        if (LOOKS.indexOf(lp.value) < 0) return;
        root.setAttribute("data-skin", lp.value);
        try {
          if (lp.value === root.getAttribute("data-skin-default")) localStorage.removeItem("site-portal-look"); else localStorage.setItem("site-portal-look", lp.value);
        } catch (e) { /* kept for this page only */ }
        try { document.dispatchEvent(new CustomEvent("portal-look", {detail: lp.value})); } catch (e) { /* an old browser: the next page */ }
      });
    }
    var g = document.getElementById("theme");
    if (!g) return;
    var c = g.querySelector('input[value="' + (root.getAttribute("data-theme") || "auto") + '"]');
    if (c) c.checked = true;
    g.addEventListener("change", function (ev) {
      var n = ev.target.value;
      if (n === "auto") root.removeAttribute("data-theme"); else root.setAttribute("data-theme", n);
      try { if (n === "auto") localStorage.removeItem("site-report-theme"); else localStorage.setItem("site-report-theme", n); } catch (e) { /* kept for this page only */ }
    });
  });
})();
