/* The Network map job's history. The map below owns its SolarWinds polling and rendering. */
(() => {
  'use strict';
  const root = document.getElementById('netmap-history');
  if (!root) return;
  const severity = {Down: 'critical', Warning: 'warning', Up: 'ok', Unknown: 'info'};
  const opened = new Set();
  const tip = document.createElement('div');
  tip.className = 'nmh-tip';
  tip.hidden = true;
  tip.setAttribute('role', 'tooltip');
  document.body.append(tip);
  const overlay = svg('svg', {'aria-hidden': 'true'});
  const card = svg('g', {});
  const background = svg('rect', {height: 50, rx: 9});
  const timeLabel = svg('text', {x: 10, y: 19, class: 'nmh-card-time'});
  const reading = svg('text', {x: 10, y: 38});
  card.append(background, timeLabel, reading);
  overlay.append(card);
  tip.append(overlay);
  let anchor;
  function hideTip() {
    tip.hidden = true;
    anchor = null;
  }
  function showTip(target, event) {
    if (!target || !target.hasAttribute('data-card')) return;
    hideTip();
    anchor = target;
    const [at, ...detail] = anchor.getAttribute('aria-label').split(' · ');
    timeLabel.textContent = at;
    reading.textContent = detail.join(' · ');
    tip.hidden = false;
    const width = Math.ceil(Math.max(timeLabel.getComputedTextLength(), reading.getComputedTextLength())) + 20;
    background.setAttribute('width', width);
    const box = target.getBoundingClientRect();
    const x = event.clientX || box.right, y = event.clientY || box.top;
    const left = Math.max(4, Math.min(x + 8, document.documentElement.clientWidth - width - 4));
    card.setAttribute('transform', 'translate(' + left + ' ' + Math.max(4, y - 55) + ')');
  }
  for (const event of ['pointerover', 'focusin']) {
    root.addEventListener(event, e => showTip(e.target.closest('[data-card]'), e));
  }
  window.addEventListener('scroll', () => {
    if (anchor && anchor === document.activeElement) showTip(anchor, {}); else hideTip();
  }, {passive: true});
  window.addEventListener('resize', hideTip);
  for (const event of ['pointerout', 'focusout']) {
    root.addEventListener(event, e => {
      if (anchor && !anchor.contains(e.relatedTarget)) hideTip();
    });
  }
  root.addEventListener('keydown', e => { if (e.key === 'Escape') hideTip(); });

  function el(tag, cls, ...content) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    node.append(...content);
    return node;
  }
  function svg(tag, attrs, text) {
    const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function table(headers, wide = []) {
    const body = el('tbody'), head = el('tr');
    headers.forEach((h, i) => head.append(el('th', wide.includes(i) ? 'nmh-wide' : '', h)));
    const wrap = el('div', 'tw', el('table', '', el('thead', '', head), body));
    return {wrap, body};
  }
  function line(body, values) {
    const tr = el('tr');
    values.forEach(v => tr.append(el('td', '', v)));
    body.append(tr);
    return tr;
  }
  function section(title, count, hint) {
    const h = el('h2', '', title);
    if (count != null) h.append(el('span', 'count', count));
    if (hint) h.append(el('span', 'nmh-hint', hint));
    return el('section', 'sec', h);
  }
  const pct = v => v == null ? '—' : v + '%';
  const duration = m => m < 60 ? m + ' min' : Math.floor(m / 60) + ' h ' + String(m % 60).padStart(2, '0') + ' min';
  function chip(state) {
    const icon = el('span', '', {Down: '×', Up: '✓', Warning: '!', Unknown: '?'}[state]);
    icon.setAttribute('aria-hidden', 'true');
    return el('span', 'chip s-' + (severity[state] || 'info'), icon, state);
  }
  let formatter;
  const when = t => formatter.format(new Date(t * 1000));
  function keyLegend(items) {
    return el('p', 'nmh-leg', ...items.map(([cls, word]) => el('span', '', el('i', cls), word)));
  }
  function strip(row, model) {
    const times = model.times;
    if (!times.length) return 'No reads';
    const n = times.length;
    const plot = svg('svg', {viewBox: '0 0 ' + n * 5 + ' 24', preserveAspectRatio: 'none',
      class: 'nmh-strip', role: 'group', 'aria-label': row.label + ', last 24 hours'});
    if (model.missed.length) {
      const gaps = svg('use', {href: '#nmh-missed', class: 'bar gap'});
      gaps.append(svg('title', {}, 'Missed read'));
      plot.append(gaps);
    }
    row.strip.forEach(bar => {
      const i = bar.i;
      const down = bar.status === 'Down', hot = bar.height > 80 && !down, gap = !severity[bar.status];
      const cls = hot ? 'warning' : severity[bar.status] || 'gap';
      const h = down ? 22 : gap ? 8 : bar.height == null ? (row.kind === 'Site' ? 21 : 4) : Math.max(3, 22 * bar.height / 100);
      const word = when(times[i]) + ' · ' + bar.status + (hot ? ' · over 80%' : '') +
        (row.kind === 'WAN link' && !gap ? ' · in ' + pct(bar.in) + ' / out ' + pct(bar.out) : '');
      const g = svg('g', {tabindex: 0, role: 'img', 'aria-label': word, 'data-card': '',
        id: 'nmh-bar-' + row.kind.replace(' ', '-') + '-' + encodeURIComponent(row.key) + '-' + times[i]});
      g.append(svg('rect', {x: i * 5, y: 0, width: 5, height: 24, class: 'hit'}),
        svg('rect', {x: i * 5, y: 23 - h, width: 4, height: h, rx: 0.8, class: 'bar ' + cls}));
      plot.append(g);
    });
    return plot;
  }

  function chart(model, row) {
    const link = !!row, values = link ? row.week : model.worst;
    const W = 1000, H = link ? 140 : 175, base = H - 20, L = 38, bw = (W - L) / values.length;
    const max = link ? 100 : Math.max(4, ...values.map(v => v ? v[0] + v[1] : 0));
    const y = v => base - v / max * (base - 8);
    const caption = link ? row.label + ', peak in and out, last 7 days' : 'Down or warning, last 7 days';
    const plot = svg('svg', {viewBox: '0 0 ' + W + ' ' + H, role: 'group', 'aria-label': caption});
    for (const v of [0, Math.round(max / 2), max]) {
      plot.append(svg('line', {x1: L, x2: W, y1: y(v), y2: y(v), class: 'grid'}),
        svg('text', {x: L - 6, y: y(v) + 4, 'text-anchor': 'end'}, v + (link ? '%' : '')));
    }
    if (link) plot.append(svg('line', {x1: L, x2: W, y1: y(80), y2: y(80), class: 'cap'}));
    const data = table(link ? ['Window begins', 'Peak in', 'Peak out', 'Down read'] : ['Window begins', 'Down', 'Warning']);
    const hits = [], paths = {in: '', out: ''};
    values.forEach((v, i) => {
      const at = model.week[i], x = L + i * bw, label = when(at);
      let words = 'No observation';
      if (link && v) {
        if (v.down) plot.append(svg('rect', {x, y: 5, width: bw, height: base - 5, class: 'band'}));
        words = 'In ' + pct(v.in) + ' · out ' + pct(v.out) + (v.down ? ' · down in this window' : '');
      } else if (v) {
        words = v[0] + ' down · ' + v[1] + ' warning';
        [0, 1].forEach(k => {
          const bottom = k ? v[0] : 0;
          if (v[k]) plot.append(svg('rect', {x: x + 1, width: bw - 2, y: y(bottom + v[k]),
            height: y(bottom) - y(bottom + v[k]), class: k ? 'warning' : 'critical'}));
        });
      }
      if (link) {
        for (const key of ['in', 'out']) {
          if (v && v[key] != null) {
            const pt = (x + bw / 2).toFixed(1) + ' ' + y(v[key]).toFixed(1);
            paths[key] += (i && values[i - 1] && values[i - 1][key] != null ? 'L' : 'M') + pt + 'l0.1 0';
          }
        }
      }
      line(data.body, link ? [label, pct(v && v.in), pct(v && v.out), v ? (v.down ? 'Yes' : 'No') : words] :
        [label, v ? v[0] : words, v ? v[1] : '—']);
      if (i % 12 === 0 && i < values.length - 3) {
        plot.append(svg('text', {x, y: H - 4}, label.split(',')[0]));
      }
      hits.push(svg('rect', {x, y: 0, width: bw, height: base, class: 'hit', role: 'img',
        'aria-label': label + ' · ' + words, 'data-card': ''}));
    });
    if (link) for (const key of ['out', 'in']) plot.append(svg('path', {d: paths[key], class: 'ln-' + key}));
    plot.append(...hits);
    const summary = el('summary', '', 'Two-hour values');
    summary.id = link ? 'nmh-values-' + encodeURIComponent(row.key) : 'nmh-map-values';
    return [el('div', link ? 'nmh-plot' : 'card nmh-plot', plot), el('details', 'nmh-values', summary, data.wrap)];
  }

  function outageTable(model, row) {
    const t = table(['What', 'Which', 'From', 'Back', 'Down for']);
    const match = e => !row || (row.key === e.key && row.kind === e.kind);
    const which = e => e.ends ? el('div', '', e.label, el('small', '', e.ends)) : e.label;
    model.outages.filter(match).forEach(e => line(t.body, [e.kind, which(e),
      (e.earlier_start ? 'By ' : '') + when(e.start), e.back ? when(e.back) : e.end,
      (e.earlier_start ? 'At least ' : '') + duration(e.minutes)]));
    model.days.filter(match).forEach(d => line(t.body, [d.kind, which(d), when(d.day),
      'UTC day total · ' + d.count + (d.count === 1 ? ' outage' : ' outages'), duration(d.minutes)]));
    return t.body.children.length ? t.wrap : el('p', 'nmh-muted', 'No outages recorded in the last 7 days.');
  }

  function elements(model, links, live) {
    const rows = links ? model.links : model.sites;
    const sec = section(links ? 'WAN links' : 'Sites', rows.length, links ?
      'Worst first · one bar per read, oldest left · height is the busier direction · ▸ opens its 7 days' :
      'One row per site · node counts from the same read');
    const t = table([links ? 'Link' : 'Site', live ? 'Now' : 'Last observed', 'Since', links ? 'In / out' : 'Nodes',
      'Last 24 h', 'Outages, 7 d', 'Down, 7 d'], [3, 5, 6]);
    t.wrap.classList.add('nmh-elements');
    sec.append(t.wrap);
    rows.forEach((r, i) => {
      const name = el('div', 'nmh-name');
      const toggle = el('button', 'nmh-toggle', '▸');
      if (links) {
        toggle.type = 'button';
        toggle.id = 'nmh-toggle-' + encodeURIComponent(r.key);
        toggle.setAttribute('aria-label', [r.label, r.ends, 'last 7 days'].filter(Boolean).join(', '));
        toggle.setAttribute('aria-controls', 'nmh-link-' + i);
        name.append(toggle);
      }
      name.append(el('b', '', r.label), el('small', '', links ? [r.node_a, r.node_b, r.speed].filter(Boolean).join(' · ') :
        r.nodes + ' nodes · ' + r.wan_links + ' WAN links'));
      const state = el('div', '', chip(r.status));
      if (!live) state.append(el('small', '', 'as of ' + when(r.read_at)));
      const numbers = links ? pct(r.in_pct) + ' / ' + pct(r.out_pct) : r.down + ' down · ' + r.warning + ' warning';
      const tr = line(t.body, [name, state, (r.since_limited ? 'By ' : '') + when(r.since) + ' · ' + r.streak + (r.since_limited ? '+ reads' : ' reads'), numbers,
        strip(r, model), r.outages, duration(r.down_min)]);
      name.append(el('span', 'nmh-narrow', numbers + ' · ' + r.outages + (r.outages === 1 ? ' outage · ' : ' outages · ') + duration(r.down_min) + ' down'));
      [3, 5, 6].forEach(k => tr.cells[k].className = 'nmh-wide');
      tr.cells[4].className = 'nmh-strip-cell';
      tr.className = 'hot-' + severity[r.status];
      if (links) {
        tr.classList.add('nmh-open');
        const detail = el('tr', 'nmh-detail'), cell = el('td');
        cell.colSpan = 7;
        detail.id = 'nmh-link-' + i;
        detail.append(cell);
        t.body.append(detail);
        function expand(open) {
          toggle.setAttribute('aria-expanded', String(open));
          detail.hidden = !open;
          if (open && !cell.hasChildNodes()) {
            const [plot, values] = chart(model, r);
            cell.append(el('div', 'nmh-detail-body', el('b', '', r.label + ' · the last 7 days'),
              el('span', 'nmh-hint', ' In (solid) and out (dashed), peak of each 2 hours · red bands mark down reads'),
              plot, outageTable(model, r), values));
          }
        }
        expand(opened.has(r.key));
        tr.addEventListener('click', e => {
          if (e.target.closest('.nmh-strip')) return;
          if (detail.hidden) opened.add(r.key); else opened.delete(r.key);
          expand(detail.hidden);
        });
      }
    });
    if (!rows.length) sec.append(el('p', 'nmh-muted', 'No ' + (links ? 'WAN links' : 'sites') + ' in the last successful read.'));
    if (links) sec.append(keyLegend([['ok', 'Up'], ['warning', 'Warning / over 80%'], ['critical', 'Down'], ['gap', 'Unknown / gap']]));
    return sec;
  }

  function glance(m, live) {
    const sec = section('At a glance'), tiles = el('div', 'tiles nmh-tiles');
    function tile(k, value, note, tone = '') {
      tiles.append(el('div', 'tile', el('div', 'k', k), el('div', 'v ' + tone, value), el('div', 'n', note)));
    }
    const all = [...m.links, ...m.sites], {links, sites, reads} = m.totals;
    const since = row => row && row.status === 'Down' ? row.label + (row.since_limited ? ' by ' : ' since ') + when(row.since) : '';
    const worst = ['Down', 'Warning', 'Unknown', 'Up'].find(v => all.some(r => r.status === v)) || 'Unknown';
    const asOf = m.as_of ? 'as of ' + when(m.as_of) : 'No successful read';
    tile(live ? 'Status now' : 'Last observed status', chip(worst), live ? links.down + ' WAN links and ' + sites.down + ' sites down' : asOf);
    const observed = value => m.as_of == null ? 'Unknown' : value;
    tile('Sites · nodes', observed(sites.total + ' · ' + sites.nodes), asOf);
    tile('Sites down', observed(sites.down), since(m.sites[0]), sites.down ? 'c-critical' : '');
    tile('WAN links down', observed(links.down + ' of ' + links.total), since(m.links[0]), links.down ? 'c-critical' : '');
    const nw = sites.warnings, lw = links.warnings;
    tile('Warnings', observed(nw + lw), m.as_of == null ? asOf : nw + ' nodes · ' + lw + ' WAN links', nw + lw ? 'c-warning' : '');
    tile('Outages, 7 days', (m.partial_day ? 'At least ' : '') + (links.outages + sites.outages),
      links.outages + ' WAN link outages · ' + sites.outages + ' site outages');
    const longest = m.outages.reduce((a, b) => !a || b.minutes > a.minutes ? b : a, null);
    const lowerBound = m.days.length || m.partial_day || (longest && longest.earlier_start);
    tile('Longest outage, 7 days', longest ? (lowerBound ? 'At least ' : '') + duration(longest.minutes) : '—', longest ?
      longest.label + ' · ' + (longest.back ? 'back ' + when(longest.back) : longest.end) : lowerBound ? 'Only daily totals available' : 'No recorded outage');
    tile('Netmap reads, 24 h', m.reads_ok + ' of ' + reads.total, (reads.total - m.reads_ok) + ' missed · usual gap ' + duration(Math.round(m.gap / 60)));
    sec.append(tiles);
    return sec;
  }

  const mapTick = document.querySelector('#nm-live .nm-tick');
  function solar() {
    const label = root.querySelector('.nmh-solar');
    if (mapTick && label) label.textContent = 'SolarWinds status · ' + mapTick.textContent;
  }
  if (mapTick) new MutationObserver(solar).observe(mapTick, {childList: true, subtree: true, characterData: true});
  function message(text) {
    hideTip();
    root.replaceChildren(el('p', 'nmh-muted', text));
  }
  let clockOffset = 0;
  const serverNow = () => (Date.now() + clockOffset) / 1000;
  function render(m) {
    if (m.message || m.loading) { message(m.message || 'Reading network map history…'); return; }
    const focusId = root.contains(document.activeElement) && document.activeElement.id;
    const expanded = Array.from(root.querySelectorAll('details[open] > summary'), s => s.id);
    formatter = new Intl.DateTimeFormat('en-GB', {timeZone: m.zone, weekday: 'short', day: '2-digit', month: 'short',
      hour: '2-digit', minute: '2-digit', timeZoneName: 'short'});
    const live = !m.stale && !!m.read.ok;
    const state = m.stale ? 'Stale' : live ? 'Live' : 'Missed read';
    const at = m.read.read_at, age = Math.max(0, Math.floor((serverNow() - at) / 60));
    const map = el('a', '', 'The map →'); map.href = '#nm-live';
    const nav = el('div', 'r', map);
    if (m.report) { const a = el('a', '', 'The full report →'); a.href = m.report.url; nav.append(a); }
    const stamp = el('span', 'nmh-stamp', 'Last read ' + age + ' min ago · ' + when(at) +
      (!m.read.ok ? ' · missed' : '') + (live ? ' · next expected ' + when(at + m.gap) : ''));
    const top = el('div', 'card strip', el('div', 'l', el('i', 'nmh-dot ' + (live ? 'ok' : 'gap')), el('b', '', state),
      stamp, el('span', 'nmh-solar')), nav);
    if (m.missed.length) {
      const axis = svg('svg', {width: 0, height: 0, 'aria-hidden': 'true'});
      const defs = svg('defs', {});
      defs.append(svg('path', {id: 'nmh-missed', d: m.missed.map(i => 'M' + i * 5 + ' 15h4v8h-4z').join('')}));
      axis.append(defs);
      top.append(axis);
    }
    const cut = [];
    for (const [key, label] of [['reads', 'latest reads in the strips'], ['links', 'WAN links'], ['sites', 'sites']]) {
      const {shown, total} = m.totals[key];
      if (shown < total) cut.push(shown + ' of ' + total + ' ' + label);
    }
    const week = section('Down or warning, last 7 days', null, 'The worst read of each 2 hours');
    const [plot, values] = chart(m);
    week.append(plot, keyLegend([['critical', 'Down (elements)'], ['warning', 'Warning (elements)']]), outageTable(m), values);
    hideTip();
    root.replaceChildren(top, glance(m, live), ...(cut.length ? [el('p', 'nmh-muted', 'Showing ' + cut.join(' · ') + '.')] : []),
      elements(m, true, live), elements(m, false, live), week,
      el('p', 'nmh-foot', (m.partial_day ? 'The first partial archived day is excluded from weekly totals. ' : '') + 'From the netmap job’s reads in the report database, checked every minute while this page is open. ' +
        'An outage is a WAN link or site down for two consecutive reads. Read only.'));
    for (const id of expanded) {
      const summary = document.getElementById(id);
      if (summary) summary.parentNode.open = true;
    }
    if (focusId) {
      const control = document.getElementById(focusId);
      if (control) control.focus({preventScroll: true});
    }
    solar();
  }

  let etag = '', model = null, timer, pending = false, generation = 0;
  function schedule(ms) { clearTimeout(timer); timer = setTimeout(poll, ms); }
  async function poll() {
    if (pending || document.hidden) return;
    pending = true;
    const request = generation;
    const ctl = new AbortController(), timeout = setTimeout(() => ctl.abort(), 15000);
    try {
      const res = await fetch(root.dataset.src, {headers: etag ? {'If-None-Match': etag} : {}, signal: ctl.signal, cache: 'no-store'});
      if (request !== generation) return;
      const date = Date.parse(res.headers.get('Date'));
      if (Number.isFinite(date)) clockOffset = date - Date.now();
      if (res.status !== 304) {
        if (!res.ok) throw Error();
        const data = await res.json();
        if (request !== generation) return;
        render(data);
        model = data;
        etag = res.headers.get('ETag') || '';
      }
      schedule(model && model.loading ? 1500 : 60000);
    } catch (_) {
      etag = ''; model = null;
      message('Network map history could not be refreshed. Waiting for the portal.');
      schedule(15000);
    } finally {
      clearTimeout(timeout);
      pending = false;
      if (request !== generation && !document.hidden) schedule(0);
    }
  }
  // A 304 can keep its body for many polls. Age its timestamp even between database reads.
  setInterval(() => {
    if (document.hidden || !model || !model.read) return;
    const age = serverNow() - model.read.read_at;
    if (!model.stale && age > model.stale_after) {
      model.stale = true;
      model.outages.forEach(o => { if (o.end === 'Still down') o.end = 'Last seen down'; });
      render(model);
    } else {
      const stamp = root.querySelector('.nmh-stamp');
      if (stamp) {
        const text = stamp.textContent.replace(/^Last read \d+/, 'Last read ' + Math.max(0, Math.floor(age / 60)));
        if (stamp.textContent !== text) stamp.textContent = text;
      }
    }
  }, 1000);
  document.addEventListener('visibilitychange', () => {
    generation++;
    if (!document.hidden) {
      model = null; etag = '';
      message('Refreshing network map history…');
      schedule(0);
    }
  });
  poll();
})();
