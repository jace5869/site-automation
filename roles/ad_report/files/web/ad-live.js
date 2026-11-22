/* 1.101.2: the live AD page (/portal/ad, roles/site_portal_stack): the AD job's newest page, kept up to date while it is open.
   Asks the portal's /api/ad every poll seconds (an ETag: a 304 when nothing changed). AD checks: as of the AD job's run (a
   newer run: "Show it", or at once when the tab is not in front). SolarWinds parts (From SolarWinds, its tile): read by the
   portal's server every portal_ad_seconds - their cells are written again in place, a DC that changes is said in a toast and
   its row flashes once. No motion with report_motion off or reduced motion. Never an inline script or style (the CSP). */
(function () {
  'use strict';
  var meta = document.querySelector('meta[name="ad-live"]');
  if (!meta || !window.fetch) return;
  var API = meta.getAttribute('content'), PORTAL = meta.getAttribute('data-portal') || '/', TAG = meta.getAttribute('data-tag') || '';
  var SEC = 'From SolarWinds', TILE = 'SolarWinds: down or warning';
  var FIELDS = ['SolarWinds', 'AD monitors (SAM)', 'CPU / memory (%)', 'Alerts', 'What it means'];   // Last down: the run's (events are not read live)
  var WORDS = { critical: 'Critical', warning: 'Warning', ok: 'OK' };
  var KEY = 'ad-live-scroll';
  function still() {
    return document.documentElement.classList.contains('motion-off') ||
      !!(window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches);
  }
  function el(t, c, kids) {
    var e = document.createElement(t);
    if (c) e.className = c;
    (kids || []).forEach(function (k) { if (k != null) e.appendChild(typeof k === 'string' ? document.createTextNode(k) : k); });
    return e;
  }
  function ago(s) {
    if (s == null) return '';
    if (s < 60) return 'just now';
    var m = Math.round(s / 60);
    return m < 60 ? m + ' min ago' : (Math.round(m / 6) / 10) + ' h ago';
  }
  function hrs(m) { return m < 60 ? m + ' min' : (Math.round(m / 6) / 10) + ' h'; }

  /* a newer run was shown with "Show it": back to where the reader was */
  try {
    var back = sessionStorage.getItem(KEY);
    if (back) { sessionStorage.removeItem(KEY); window.scrollTo(0, parseInt(back, 10) || 0); }
  } catch (x) { /* no storage: the top */ }

  /* ---- the strip under the title ---------------------------------------------------------------------------------------- */
  var h1 = document.querySelector('h1');
  var strip = el('div', 'adl-strip'), dot = el('span', 'adl-dot'), swT = el('span', 'adl-part'), adT = el('span', 'adl-part');
  var home = el('a', 'adl-home', ['Portal']);
  home.href = PORTAL;
  strip.setAttribute('role', 'status');
  strip.appendChild(dot);
  strip.appendChild(el('b', '', ['Live on the portal']));
  strip.appendChild(swT);
  strip.appendChild(adT);
  strip.appendChild(home);
  var note = el('p', 'adl-note');
  note.hidden = true;
  var newer = el('p', 'adl-newer');
  newer.hidden = true;
  var show = el('button', 'adl-show', ['Show it']);
  show.type = 'button';
  show.addEventListener('click', function () { reload(); });
  swT.textContent = 'SolarWinds parts: reading…';
  adT.textContent = 'AD checks: reading…';
  if (h1 && h1.parentNode) {
    var after = h1.nextElementSibling && h1.nextElementSibling.tagName === 'P' ? h1.nextElementSibling : h1;
    after.parentNode.insertBefore(newer, after.nextSibling);
    after.parentNode.insertBefore(note, after.nextSibling);
    after.parentNode.insertBefore(strip, after.nextSibling);
  } else {
    document.body.insertBefore(newer, document.body.firstChild);
    document.body.insertBefore(note, document.body.firstChild);
    document.body.insertBefore(strip, document.body.firstChild);
  }
  var toasts = el('div', 'adl-toasts');
  toasts.setAttribute('aria-live', 'polite');
  document.body.appendChild(toasts);
  if (!still()) document.documentElement.classList.add('adl-move');

  function reload() {
    try { sessionStorage.setItem(KEY, String(window.scrollY || 0)); } catch (x) { /* none */ }
    location.reload();
  }

  /* ---- From SolarWinds: the section, its rows, its tile ------------------------------------------------------------------- */
  function section() {
    return [].slice.call(document.querySelectorAll('details.sec')).filter(function (d) {
      var h = d.querySelector('h2');
      return h && h.firstChild && String(h.firstChild.textContent).trim() === SEC;
    })[0];
  }
  function badge(sec, text, fresh) {
    if (!sec) return;
    var h = sec.querySelector('h2'), b = h && h.querySelector('.adl-badge');
    if (!h) return;
    if (!b) { b = el('span', 'adl-badge'); h.appendChild(b); }
    b.className = 'adl-badge' + (fresh ? ' fresh' : '');
    b.textContent = text;
  }
  function rows(sec) {
    var t = sec && sec.querySelector('table.xt');
    if (!t || !t.tBodies[0]) return [];
    return [].slice.call(t.tBodies[0].rows).filter(function (r) { return !r.classList.contains('xr'); });
  }
  function dcOf(tr) {
    var c = tr.querySelector('td[data-l="DC"]');
    return c ? String(c.textContent).trim().toLowerCase() : '';
  }
  function cell(td, text, st) {
    td.replaceChildren();
    if (st) {
      var chip = el('span', 'chip s-' + st, [el('span', '', [text])]);
      td.appendChild(chip);
    } else {
      td.appendChild(document.createTextNode(text));
    }
    td.setAttribute('data-v', text);
  }
  function flash(tr) {
    if (still()) return;
    tr.classList.remove('adl-flash');
    void tr.offsetWidth;                                   // the animation again for a second change
    tr.classList.add('adl-flash');
  }
  var last = null;                                         // the last answer's cells by DC: {dc: {SolarWinds, AD monitors (SAM)}}
  function patch(sw) {
    var sec = section(), s = sw.section;
    if (!sec || !s) return;
    var ci = {};
    s.columns.forEach(function (c, i) { ci[c] = i; });
    var by = {};
    s.rows.forEach(function (r, i) { by[String(r[ci.DC]).toLowerCase()] = i; });
    var now = {}, said = [];
    rows(sec).forEach(function (tr) {
      var dc = dcOf(tr), i = by[dc];
      if (i == null) return;                               // a DC this run does not have: its next run
      var r = s.rows[i], cs = s.cell_status[i] || [];
      now[dc] = {};
      FIELDS.forEach(function (f) {
        if (ci[f] == null) return;
        var td = tr.querySelector('td[data-l="' + f + '"]');
        var text = String(r[ci[f]] == null ? '' : r[ci[f]]), st = cs[ci[f]] || '';
        now[dc][f] = text;
        var chip = td && td.querySelector('.chip'), cur = chip ? ((chip.className.match(/\bs-(\w+)/) || [])[1] || '') : '';
        if (td && (td.getAttribute('data-v') !== text || cur !== st)) cell(td, text, st);
      });
      tr.className = tr.className.replace(/\br-\w+\b/g, '').trim();
      if (s.row_status[i]) tr.classList.add('r-' + s.row_status[i]);
      var was = last && last[dc];
      if (was) {
        ['SolarWinds', 'AD monitors (SAM)'].forEach(function (f) {
          if (now[dc][f] != null && was[f] != null && now[dc][f] !== was[f]) {
            said.push({ dc: r[ci.DC], what: f === 'SolarWinds' ? 'SolarWinds: ' + now[dc][f] : 'AD monitors: ' + now[dc][f], was: was[f],
              bad: /critical|warning/.test(cs[ci[f]] || '') });
            flash(tr);
          }
        });
      }
    });
    last = now;
    var p = sec.querySelector('.body > p, .body > p.st');
    if (p && s.text) p.textContent = s.text;
    var hs = sec.querySelector('summary .chip, summary .stc');
    if (hs && WORDS[s.status]) {
      hs.className = hs.className.replace(/\bs-\w+\b/g, 's-' + s.status);
      var w = hs.querySelector('span:last-child') || hs;
      if (/^(Critical|Warning|OK)$/.test(String(w.textContent).trim())) w.textContent = WORDS[s.status];
    }
    tile(sw.tile);
    said.slice(0, 3).forEach(toast);
  }
  function tile(t) {
    if (!t) return;
    var c = [].slice.call(document.querySelectorAll('.tile .tcopy')).filter(function (b) { return b.getAttribute('aria-label') === 'Copy ' + TILE; })[0];
    var box = c && c.closest('.tile'), v = box && box.querySelector('.v');
    if (!v) return;
    v.textContent = String(t.value);
    v.className = v.className.replace(/\bc-\w+\b/g, '').trim() + ' c-' + t.status;
    box.className = box.className.replace(/\bhot-\w+\b/g, '').trim() + (t.status === 'ok' ? '' : ' hot-' + t.status);
    c.setAttribute('data-copy', TILE + ': ' + t.value);
  }
  function toast(x) {
    var box = el('div', 'adl-toast' + (x.bad ? ' bad' : ''), [el('div', '', [el('div', 't', [x.dc + ': ' + x.what]), el('div', 'm', ['was ' + x.was])])]);
    var close = el('button', 'x', ['×']);
    close.type = 'button';
    close.setAttribute('aria-label', 'Close');
    close.addEventListener('click', function () { box.remove(); });
    box.appendChild(close);
    while (toasts.children.length >= 3) toasts.removeChild(toasts.firstChild);
    toasts.appendChild(box);
    setTimeout(function () { box.remove(); }, 15000);
  }

  /* ---- the answer ---------------------------------------------------------------------------------------------------------- */
  var etag = '', poll = 60, timer = null, got = Date.now();
  function apply(a) {
    var gone = Math.max(0, Math.round((Date.now() - got) / 1000));     // since the answer: a 304 keeps it, the ages move on
    poll = Math.max(15, Math.min(300, a.poll || 60));
    var run = a.run, sw = a.sw || {};
    dot.className = 'adl-dot' + (run && !run.old && sw.on && !sw.stale ? '' : ' off');
    if (run) {
      var t = 'AD checks: as of ' + run.as_of + ' (the AD job' + (run.next ? '; next ' + run.next : '') + ')';
      if (run.old) t = 'AD checks: as of ' + run.as_of + ' - ' + hrs(run.age_min + Math.floor(gone / 60)) + ' old: the AD job has not run since';
      adT.textContent = t;
      adT.className = 'adl-part' + (run.old ? ' warn' : '');
      if (run.tag && TAG && run.tag !== TAG) {
        if (document.hidden) { reload(); return; }
        newer.replaceChildren(el('span', '', ['A newer AD run is ready (as of ' + run.as_of + '). ']), show);
        newer.hidden = false;
      }
    } else {
      adT.textContent = 'AD checks: as of this page';
      adT.className = 'adl-part warn';
    }
    note.textContent = a.why || '';
    note.hidden = !a.why;
    var sec = section();
    if (!sw.on) {
      swT.textContent = 'SolarWinds parts: as of the AD run (' + sw.why + ')';
      swT.className = 'adl-part';
      badge(sec, 'as of the AD run', false);
    } else if (sw.section) {
      swT.textContent = 'SolarWinds parts: updated ' + ago(sw.age + gone) + ' (read every ' + Math.round(sw.every / 60) + ' min)' +
        (sw.stale ? ' - not read now: ' + sw.stale : '');
      swT.className = 'adl-part' + (sw.stale ? ' warn' : '');
      badge(sec, 'updated ' + ago(sw.age + gone), !sw.stale);
      patch(sw);
    } else {
      swT.textContent = 'SolarWinds parts: as of the AD run' + (sw.stale ? ' - SolarWinds not read: ' + sw.stale : ' - reading SolarWinds…');
      swT.className = 'adl-part' + (sw.stale ? ' warn' : '');
      badge(sec, 'as of the AD run', false);
    }
  }
  var shown = null;
  function ask() {
    var h = etag ? { 'If-None-Match': etag } : {};
    fetch(API, { headers: h, credentials: 'same-origin', cache: 'no-store' }).then(function (r) {
      if (r.status === 304) return null;
      if (!r.ok) throw new Error('the portal answered ' + r.status);
      etag = r.headers.get('ETag') || '';
      got = Date.now();
      return r.json();
    }).then(function (a) {
      if (a) { shown = a; apply(a); } else if (shown) { apply(shown); }    // the ages move on
    }).catch(function (e) {
      dot.className = 'adl-dot off';
      note.textContent = 'The portal did not answer (' + e.message + '): the page stays as it is, tried again in ' + poll + ' s.';
      note.hidden = false;
    }).then(function () {
      clearTimeout(timer);
      timer = setTimeout(ask, poll * 1000);
    });
  }
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden && shown && shown.run && TAG && shown.run.tag !== TAG) reload();
  });
  window.adLive = { apply: apply, section: section };      // the tests
  ask();
})();
