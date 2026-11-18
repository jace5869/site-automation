// The colours chosen last time (Auto, Light or Dark), set before the page is drawn - one choice for the portal and the report
// website (the same key as the report pages: site-report-theme). Its radio buttons work by themselves (CSS); this keeps the choice.
(function () {
  "use strict";
  var root = document.documentElement;
  try {
    var t = localStorage.getItem("site-report-theme");
    if (t === "light" || t === "dark") root.setAttribute("data-theme", t);
  } catch (e) { /* no storage: Auto */ }
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
