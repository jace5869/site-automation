/* netmap-live.js - the site portal's live Network map (docs/NETWORK_MAP.md, "Live on the portal"): asks the portal for the
   map (its JSON, read from SolarWinds by the portal's server every netmap_live_seconds into one shared cache - never per
   viewer) a few seconds after each new read, and hands it to the map (NM.load). What changed is drawn there (a node that
   went down pulses red, a link that dropped stops and turns red) and said here: a toast, and the "updated N s ago" line.
   The request carries the last answer's ETag: an unchanged map costs a 304. No inline script, no HTML from strings. */
(function () {
  var box = document.getElementById('nm'), live = document.getElementById('nm-live');
  if (!box || !live || !window.NM) return;
  var url = live.getAttribute('data-src'), every = Math.max(5, +live.getAttribute('data-poll') || 15) * 1000;
  var tick = live.querySelector('.nm-tick'), toasts = document.getElementById('nm-toasts'), etag = '', readAt = 0, timer = 0, first = true;
  var stale = '', xerr = '', disk = '', links = '', cut = false;   // disk: 1.95.0 - the map the server kept (on disk), as of its time, while it reads again       // the server's (it could not read SolarWinds) and ours (the portal did not answer): each cleared on its own
  function ago() {
    var why = xerr || stale, linkTime = links === 'unknown' ? ' · link age unknown' : links ? ' · links as of ' + links : '';
    live.classList.toggle('stale', !!why);
    if (!readAt) { tick.textContent = why ? 'not read: ' + why : 'reading SolarWinds…'; return; }
    if (disk && !why) { tick.textContent = 'as of ' + disk + linkTime + ' · refreshing…'; return; }
    var s = Math.max(0, Math.round((Date.now() - readAt) / 1000));
    tick.textContent = (why ? 'not read again: ' + why + ' · ' : '') + 'updated ' + (s < 60 ? s + ' s' : Math.floor(s / 60) + ' min ' + (s % 60) + ' s') + ' ago' + linkTime +
      (cut ? ' · the status read stopped at netmap_max_nodes: the rest show as not known' : '');
  }
  function toast(kind, text) {
    if (!toasts) return;
    var t = document.createElement('div'); t.className = 'nm-toast ' + kind; t.setAttribute('role', 'status');
    var g = document.createElement('b'); g.textContent = kind === 'down' ? '×' : kind === 'up' ? '✓' : '!'; g.setAttribute('aria-hidden', 'true');
    var s = document.createElement('span'); s.textContent = text; t.appendChild(g); t.appendChild(s);
    toasts.appendChild(t);
    while (toasts.children.length > 4) toasts.removeChild(toasts.firstChild);
    setTimeout(function () { t.classList.add('out'); setTimeout(function () { if (t.parentNode) t.parentNode.removeChild(t); }, 400); }, 7000);
  }
  function said(ch) {
    var down = ch.filter(function (c) { return c.kind === 'down'; }), up = ch.filter(function (c) { return c.kind === 'up'; }),
        ld = ch.filter(function (c) { return c.kind === 'linkdown'; }), lu = ch.filter(function (c) { return c.kind === 'linkup'; }),
        other = ch.length - down.length - up.length - ld.length - lu.length, byId = Object.create(null);
    (window.NM.data().nodes || []).forEach(function (n) { byId[n.id] = n; });
    function names(list, f) { var n = list.slice(0, 3).map(f).join(', '); return list.length > 3 ? n + ' and ' + (list.length - 3) + ' more' : n; }
    function lname(c) { var a = byId[c.link.a], b = byId[c.link.b]; return (a ? a.name : '?') + ' ↔ ' + (b ? b.name : '?'); }
    if (down.length) toast('down', 'Down: ' + names(down, function (c) { return c.node.name + ' (' + c.node.site + ')'; }));
    if (ld.length) toast('down', 'Link down: ' + names(ld, lname));
    if (up.length) toast('up', 'Back up: ' + names(up, function (c) { return c.node.name; }));
    if (lu.length) toast('up', 'Link back up: ' + names(lu, lname));
    if (other > 0) toast('info', other + (other === 1 ? ' other change' : ' other changes') + ' (warnings, new nodes)');
  }
  function next(ms) { clearTimeout(timer); timer = setTimeout(poll, ms); }
  function poll() {
    if (document.hidden) { next(every); return; }
    var x = new XMLHttpRequest();
    x.open('GET', url, true);
    x.setRequestHeader('Accept', 'application/json');
    if (etag) x.setRequestHeader('If-None-Match', etag);
    x.onload = function () {
      if (x.status === 304) { xerr = ''; ago(); next(every); return; }
      if (x.status === 429) { next(every * 2); return; }
      if (x.status !== 200) { xerr = 'the portal answered ' + x.status; ago(); next(every * 2); return; }
      var r; try { r = JSON.parse(x.responseText); } catch (e) { xerr = 'the portal sent something else than the map'; ago(); next(every * 2); return; }
      xerr = '';
      etag = x.getResponseHeader('ETag') || '';
      readAt = r.age == null ? 0 : Date.now() - Math.max(0, +r.age) * 1000; stale = r.stale || ''; disk = r.from_disk ? (r.as_of || 'the last read') : ''; cut = !!r.cut; links = r.links_as_of || '';
      var w = document.getElementById('nm-why');
      if (r.map) {
        if (w) w.hidden = true;
        var ch = [];
        try { ch = window.NM.load(r.map); } catch (e) { xerr = 'the map could not be drawn'; }
        if (!first && ch.length) said(ch);
        first = false;
        box.removeAttribute('data-empty');
      } else if (r.why) {
        box.setAttribute('data-empty', '1'); if (w) { w.textContent = r.why; w.hidden = false; }
      }
      ago();
      // the next look: shortly after the server's next read, never sooner than 5 s
      next(r.next ? Math.max(5000, Math.min(every * 4, r.next * 1000 + 1500)) : every);
    };
    x.onerror = function () { xerr = 'the portal did not answer'; ago(); next(every * 2); };
    x.send();
  }
  setInterval(ago, 1000);
  document.addEventListener('visibilitychange', function () { if (!document.hidden) next(200); });
  window.nmLiveTest = { poll: poll };
  poll();
})();
