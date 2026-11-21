// The colours chosen last time (Auto, Light or Dark), set before the page is drawn - one choice for the portal and the report
// website (the same key as the report pages: site-report-theme). Its radio buttons work by themselves (CSS); this keeps the choice.
(function () {
  "use strict";
  var root = document.documentElement;
  try {
    var t = localStorage.getItem("site-report-theme");
    if (t === "light" || t === "dark") root.setAttribute("data-theme", t);
  } catch (e) { /* no storage: Auto */ }
  // 1.87.0: motion, decided before the page is drawn (so nothing shows and then jumps to its start): portal_motion always (html.motion-always) moves,
  // off (html.motion-off) never, auto moves unless the system asks for less - and follows that setting when it changes. portal.css: html.moves.
  var cl = root.classList;
  var mq = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
  var motion = function () { cl.toggle("moves", cl.contains("motion-always") || (!cl.contains("motion-off") && !(mq && mq.matches))); };
  motion();
  if (mq && mq.addEventListener) mq.addEventListener("change", motion);
  document.addEventListener("DOMContentLoaded", function () {
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
