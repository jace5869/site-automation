/* Site portal (1.68.1): /portal/stigman/go - a STIG scorecard link (stigman_link_style: open). It keeps the link's target
   in this browser tab (sessionStorage: it survives STIG Manager's sign-in, which leaves and comes back in the same tab)
   and goes on to STIG Manager's plain address - never a # or a ? there: STIG Manager 1.6 reads those as a sign-in answer
   and stops. The script nginx adds to STIG Manager's page (stigman-open.js) opens the review; without it, or when it
   cannot, STIG Manager's home page shows, as with a plain link. */
(function () {
  var t = document.querySelector('meta[name="sa-stigman-target"]');
  var to = document.querySelector('meta[name="sa-stigman-to"]');
  var dest = (to && to.getAttribute("content")) || "/";
  if (!/^\/(?!\/)/.test(dest)) dest = "/";              // this site only
  try {
    if (t && t.getAttribute("content")) {
      var v = JSON.parse(t.getAttribute("content"));
      v.at = Date.now();
      sessionStorage.setItem("sa_stigman_open", JSON.stringify(v));
    }
  } catch (e) { /* no storage: the plain address */ }
  location.replace(dest);
})();
