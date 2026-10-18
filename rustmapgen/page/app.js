// RustMapGen panel: the maps made so far, each with its checks, its numbers and what made it.
// Plain DOM, no build step.
'use strict';

const S = {
  maps: [], recipes: [], metrics: [], cache: new Map(), install: null,
  current: null, compare: '', tab: 'gen', passingOnly: false,
  layers: { monuments: true, rails: true, roads: true, rivers: false, power: false },
  view: { k: 1, x: 0, y: 0 }, views: [], poll: null, status: null, statusAt: 0, ticker: null,
};
const LIVE = '@live';            // S.current while the map being made is shown
const START_HASH = location.hash; // read before the first map shown writes its own
const SEND = { 'Content-Type': 'application/json', 'X-RustMapGen': '1' };
const LAYERS = [['monuments', 'Monuments'], ['rails', 'Rails'], ['roads', 'Roads'], ['rivers', 'Rivers'], ['power', 'Power lines']];
const PATHS = { rails: 'Rail', roads: 'Road', rivers: 'River', power: 'Powerline' };
// Which monuments get their label first when two would overlap (render.py has the same lists).
const RANK = ['xlarge', 'large', 'medium', 'harbor', 'fishing_village', 'offshore', 'military_bases', 'arctic_bases',
  'roadside', 'small', 'jungle_ruins', 'lighthouse', 'cave', 'ice_lakes', 'swamp', 'tiny', 'underwater_lab'];
const SAFE = new Set(['compound', 'bandit_town', 'fishing_village_a', 'fishing_village_b', 'fishing_village_c', 'stables_a', 'stables_b']);
const MINOR = new Set(['tiny', 'cave', 'ice_lakes', 'underwater_lab', 'swamp', 'lighthouse', 'jungle_ruins']);
const HIDDEN = new Set(['maintainables']);

const $ = (id) => document.getElementById(id);
function el(tag, attrs, ...kids) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === false || value == null) continue;
    if (key === 'class') node.className = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else if (key === 'text') node.textContent = value;
    else node.setAttribute(key, value === true ? '' : value);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) node.append(kid);
  return node;
}
// replaceChildren writes a null as the text "null": what is not there is left out.
const fill = (node, ...kids) => node.replaceChildren(...kids.flat().filter((kid) => kid != null && kid !== false));
const fmt = (value, digits = 0) => value == null || Number.isNaN(value) ? '–'
  : Number(value).toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });
const when = (iso) => iso ? iso.slice(0, 16).replace('T', ' ') + ' UTC' : '';

async function api(path, options) {
  const response = await fetch(path, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || body.message || `request failed (${response.status})`);
  return body;
}

// ---------- list ----------
try { S.passingOnly = localStorage.getItem('rustmapgen-passing') === '1'; } catch { /* no storage: every map is shown */ }

async function load(keep, quiet) {
  const data = await api('/api/maps');
  Object.assign(S, { maps: data.maps, metrics: data.metrics, install: data.install });
  renderInstall();
  renderList();
  if (!quiet) renderStatus(data.generator);
  if (keep === LIVE || (!S.maps.length && liveItems(S.status).length)) return showLive();
  if (!S.maps.length) {
    $('head').replaceChildren(el('div', { class: 'empty', text: 'No map yet. Make one with "New Map".' }));
    for (const id of ['views', 'glance', 'd-tabs', 'panels']) $(id).replaceChildren();
    return;
  }
  const wanted = keep || decodeURIComponent(location.hash.slice(1));
  const first = S.maps.find((m) => m.name === wanted) || S.maps[0];
  await show(first.name);
}

function checkTag(card) {
  const ok = card.checks_ok === card.checks;
  return el('span', { class: 'tag ' + (ok ? 'ok' : 'bad'), text: `${card.checks_ok}/${card.checks}` });
}

function renderList() {
  const side = $('list');
  const pick = $('pick');
  side.replaceChildren();
  pick.replaceChildren();
  const maps = S.passingOnly ? S.maps.filter((card) => card.checks_ok === card.checks) : S.maps;
  side.append(el('h3', {}, `Maps · ${maps.length}`,
    el('button', { class: 'link', 'data-testid': 'passing-only', text: S.passingOnly ? `show all ${S.maps.length}` : 'passing only',
      onclick: () => { S.passingOnly = !S.passingOnly; try { localStorage.setItem('rustmapgen-passing', S.passingOnly ? '1' : ''); } catch { /* a private window: lasts for this visit */ } renderList(); } })));
  if (!maps.length) side.append(el('p', { class: 'side-empty', text: S.maps.length ? 'No map passes every check yet.' : 'Maps you make appear here.' }));
  for (const card of maps) {
    side.append(el('button', { class: 'item' + (card.name === S.current ? ' on' : ''), 'data-name': card.name, onclick: () => { showMaps(); show(card.name); } },
      el('img', { src: `/files/${card.name}/thumb.webp`, alt: '', loading: 'lazy', width: 44, height: 44 }),
      el('span', {}, el('span', { class: 't', text: card.title.split(' · seed')[0] }),
        el('span', { class: 's', text: `${card.size} · seed ${card.seed} · ${fmt(card.rail_km, 1)} km rails` })),
      checkTag(card)));
    pick.append(el('option', { value: card.name, text: `${card.title} (${card.size})` }));
  }
  $('top-counts').replaceChildren(el('b', { text: String(S.maps.length) }), S.maps.length === 1 ? ' map' : ' maps');
}

// What this computer has: shown above the settings until a map can be made.
function renderInstall() {
  const box = $('install');
  const i = S.install;
  if (!i) return;
  const problems = [];
  if (!i.installed) problems.push(`No game at ${i.game}. Run the installer (install.sh or install.ps1), or "python -m rustmapgen install".`);
  else if (i.update_unfinished) problems.push('The last update of the game did not finish: update it again (Updates, at the top).');
  else if (!i.mod) problems.push('The mod is not in the game yet: it is put there when the first map is made.');
  if (i.oxide) problems.push('The game folder has Oxide in it: the generator needs the plain game, in a folder of its own.');
  box.hidden = !problems.length && !!i.installed;
  box.className = 'panel install' + (problems.length && (!i.installed || i.oxide || i.update_unfinished) ? ' bad' : '');
  fill(box,
    el('span', {}, 'Game ', el('b', { text: i.installed ? `build ${i.build || '?'}` : 'not installed' })),
    el('span', {}, 'Free memory ', el('b', { text: i.free_gb != null ? `${fmt(i.free_gb, 1)} GB` : '–' })),
    i.cpu_c != null ? el('span', {}, 'CPU ', el('b', { text: `${i.cpu_c} °C` })) : null,
    ...problems.map((text) => el('span', { class: 'muted', text })));
}

// ---------- one map ----------
async function entry(name) {
  if (!S.cache.has(name)) S.cache.set(name, await api('/api/map?name=' + encodeURIComponent(name)));
  return S.cache.get(name);
}

async function show(name) {
  S.current = name;
  history.replaceState(null, '', '#' + encodeURIComponent(name));
  for (const item of document.querySelectorAll('.item')) item.classList.toggle('on', item.dataset.name === name);
  renderLive();
  $('pick').value = name;
  const main = await entry(name);
  if (S.current !== name) return;
  if (S.compare === name) S.compare = '';
  const other = S.compare ? await entry(S.compare).catch(() => null) : null;
  renderHead(main);
  renderGlance(main);
  renderViews(main, other);
  renderPanels(main, other);
}

async function removeMap(map) {
  if (!confirm(`Delete "${map.title}" and its files? This cannot be undone.`)) return;
  try {
    await api('/api/maps/' + encodeURIComponent(map.name), { method: 'DELETE', headers: SEND });
    S.cache.delete(map.name);
    if (S.compare === map.name) S.compare = '';
    await load(null, true);
  } catch (error) { alert(error.message); }
}

async function loadTestMap(map) {
  try {
    const answer = await api('/api/load-test', { method: 'POST', headers: SEND, body: JSON.stringify({ name: map.name }) });
    renderStatus(answer.generator);
  } catch (error) { alert(error.message); }
}

function renderHead(map) {
  const compare = el('select', { 'data-testid': 'compare', 'aria-label': 'Compare with', onchange: (e) => { S.compare = e.target.value; show(S.current); } },
    el('option', { value: '', text: 'Compare with…' }),
    S.maps.filter((m) => m.name !== map.name).map((m) => el('option', { value: m.name, text: `${m.title} (${m.size})`, selected: m.name === S.compare })));
  const tools = el('div', { class: 'tools' }, compare,
    el('a', { class: 'btn primary', href: `/files/${map.name}/final.map`, 'data-testid': 'download', text: 'Download .map' }),
    map.files && map.files.labels ? el('a', { class: 'btn', href: `/files/${map.name}/map_labels.webp`, target: '_blank', text: 'Picture' }) : null,
    el('button', { class: 'btn', text: 'Test in the game', title: 'The game loads this file as a server would, then stops', onclick: () => loadTestMap(map) }),
    el('button', { class: 'btn danger', text: 'Delete', onclick: () => removeMap(map) }));
  fill($('head'),
    el('div', { class: 'title' }, el('h1', { text: map.title }), map.note ? el('span', { class: 'note', text: map.note }) : null),
    tools,
    el('div', { class: 'layers' }, LAYERS.map(([key, label]) => el('button', { class: 'chip' + (S.layers[key] ? ' on' : ''), 'data-layer': key, 'aria-pressed': String(S.layers[key]),
      onclick: (e) => { S.layers[key] = !S.layers[key]; e.currentTarget.classList.toggle('on', S.layers[key]); e.currentTarget.setAttribute('aria-pressed', String(S.layers[key])); applyLayers(); } },
      el('i'), label))));
}

// ---------- at a glance: beside the map, worst first ----------
function glanceLoad(map) {
  const test = map.load_test;
  const again = el('button', { class: 'link', text: 'test now', onclick: () => loadTestMap(map) });
  if (!test) return ['warn', 'Not tested in the game yet', null, again];
  if (test.state === 'stale') return ['warn', 'Changed since the game test', null, again];
  if (test.state !== 'done' || !test.loaded) return ['bad', 'The game did not load it', (test.log_trouble || [])[0] || 'see Run below', again];
  if (test.loaded.prefabs !== map.facts.prefabs) return ['warn', `The game read ${fmt(test.loaded.prefabs)} of ${fmt(map.facts.prefabs)} prefabs`, null, again];
  const trouble = test.log_trouble || [];
  if (trouble.length) return ['warn', `The game loads it, with ${trouble.length} problem${trouble.length === 1 ? '' : 's'} in its log`, trouble[0], again];
  return ['ok', 'The game loads it', `nothing wrong in its log · ${fmt(test.loaded.prefabs)} prefabs, ${fmt(test.loaded.monuments)} monuments, in ${fmt(test.seconds)} s`];
}

function renderGlance(map) {
  const failed = map.checks.filter((c) => !c.ok);
  const rows = [failed.length ? ['bad', `${failed.length} of ${map.checks.length} checks fail`, 'each one below; all of them under Checks']
    : ['ok', `All ${map.checks.length} checks pass`, 'rails, terrain, monuments, tunnels and what was asked'], glanceLoad(map)];
  for (const check of failed) rows.push(['bad', check.rule, check.detail]);
  for (const m of map.merge || []) if (m.dropped) rows.push(['warn', `${m.template}: ${m.dropped} prefabs the game does not know`, 'left out of the map']);
  if (map.facts.unknown_prefabs) rows.push(['warn', `${fmt(map.facts.unknown_prefabs)} prefabs the game does not know`]);
  const order = { bad: 0, warn: 1, ok: 2 };
  rows.sort((a, b) => order[a[0]] - order[b[0]]);
  const n = map.numbers;
  const placed = Object.values(map.facts.monument_counts).reduce((sum, count) => sum + count, 0);
  const facts = [['Size', fmt(map.size)], ['Seed', String(map.seed ?? '–')],
    ['Land', n.land != null ? `${fmt(n.land, 1)} %` : null], ['Highest point', n.highest_m != null ? `${fmt(n.highest_m)} m` : null],
    ['Rails', n.rail_km != null ? `${fmt(n.rail_km, 1)} km` + (n.ring_km ? `, ring ${fmt(n.ring_km, 1)}` : ', no ring') : null],
    ['Roads', n.road_km != null ? `${fmt(n.road_km, 1)} km` : null],
    ['Monuments', `${fmt(placed)}, ${fmt(n.monument_kinds)} kinds`], ['Outpost', n.outpost_m != null ? `${fmt(n.outpost_m)} m from the middle` : 'none'],
    ['Prefabs', fmt(map.facts.prefabs)], ['Recipe', map.recipe || '–'],
    ['Made', map.created ? when(map.created) : '–'], ['Took', map.run ? `${clock(map.run.wall_seconds)} on build ${map.run.game_build || '?'}` : '–']].filter(([, value]) => value != null);
  // The monuments a player looks for first: safe zones, then the big ones, each once with its count.
  const major = new Map();
  for (const m of map.facts.monuments) {
    const safe = SAFE.has(m.prefab);
    const rank = RANK.indexOf(m.folder);
    if (!safe && (rank < 0 || rank > RANK.indexOf('arctic_bases'))) continue;
    const key = m.label.replace(/ [A-E]$/, '');
    const entry = major.get(key) || { label: key, count: 0, safe, rank: safe ? -1 : rank };
    entry.count += 1;
    major.set(key, entry);
  }
  const majorList = [...major.values()].sort((a, b) => a.rank - b.rank || a.label.localeCompare(b.label));
  fill($('glance'),
    el('div', { class: 'g-head' }, el('h2', { text: 'At a glance' })),
    el('ul', { class: 'g-status' }, rows.map(([level, text, detail, action]) => el('li', { class: 'sev-' + level },
      el('i'), el('span', {}, el('b', { text }), detail ? el('small', { text: detail }) : null), action || null))),
    el('div', { class: 'g-cols' },
      el('div', { class: 'g-block' }, el('h3', { text: 'The map' }),
        cells(facts))),
    majorList.length ? el('div', { class: 'g-block' }, el('h3', { text: 'Main monuments' }),
      el('ul', { class: 'g-mons' }, majorList.map((m) => el('li', { class: m.safe ? 'safe' : '' }, el('i'), m.label, m.count > 1 ? el('b', { text: ` ×${m.count}` }) : null)))) : null);
}

// ---------- map views ----------
const SVG = 'http://www.w3.org/2000/svg';
function linePath(flat, half) {
  let d = '';
  for (let i = 0; i + 1 < flat.length; i += 2) d += (i ? 'L' : 'M') + (flat[i] + half) + ' ' + (half - flat[i + 1]);
  return d;
}

function buildView(map) {
  // The picture has margin_m metres of sea around the square, where the oil rigs stand (none on maps drawn before).
  const side = map.size + 2 * (map.margin_m || 0);
  const half = side / 2;
  const svg = document.createElementNS(SVG, 'svg');
  svg.setAttribute('viewBox', `0 0 ${side} ${side}`);
  svg.setAttribute('preserveAspectRatio', 'none');
  for (const layer of ['power', 'rivers', 'roads', 'rails']) {
    const group = document.createElementNS(SVG, 'g');
    group.setAttribute('class', 'l-' + layer);
    const d = (map.lines[PATHS[layer]] || []).map((flat) => linePath(flat, half)).join('');
    for (const cls of layer === 'rails' ? ['under', 'over'] : ['']) {
      const path = document.createElementNS(SVG, 'path');
      path.setAttribute('d', d);
      if (cls) path.setAttribute('class', cls);
      group.append(path);
    }
    svg.append(group);
  }
  // Stretches of rail the ring does not reach (a train cannot get there): over the rails, in red.
  const cut = ((map.facts.rails || {}).loose_lines || []).map((flat) => linePath(flat, half)).join('');
  if (cut) {
    const group = document.createElementNS(SVG, 'g');
    group.setAttribute('class', 'l-rails l-cut');
    const path = document.createElementNS(SVG, 'path');
    path.setAttribute('d', cut);
    group.append(path);
    svg.append(group);
  }
  const merged = new Set((map.merge || []).filter((m) => m.copies && m.placed).map((m) => m.monument));
  const marks = el('div', { class: 'marks' });
  const points = map.facts.monuments.filter((m) => !HIDDEN.has(m.folder)).map((m) => {
    const minor = MINOR.has(m.folder);
    const dot = el('span', { class: 'mark' + (SAFE.has(m.prefab) ? ' safe' : '') + (merged.has(m.label) ? ' merged' : '') + (minor ? ' small' : '') });
    const label = el('span', { class: 'label', text: m.label });
    marks.append(dot, label);
    const rank = RANK.indexOf(m.folder);
    return { fx: (m.x + half) / side, fy: (half - m.z) / side, dot, label, rank: rank < 0 ? RANK.length : rank, width: m.label.length * 6.3 + 10 };
  }).sort((a, b) => a.rank - b.rank);
  const where = el('span', { class: 'where', text: '' });
  const stage = el('div', { class: 'stage' }, el('img', { src: `/files/${map.name}/terrain.webp`, alt: `Map ${map.title}`, draggable: 'false' }), svg);
  const name = el('span', { class: 'name', text: map.title });
  const root = el('div', { class: 'view', 'data-testid': 'view', 'data-name': map.name }, stage, marks, name, where,
    el('div', { class: 'zoom' }, el('button', { 'aria-label': 'Zoom in', text: '+', onclick: () => zoomBy(1.5) }),
      el('button', { 'aria-label': 'Zoom out', text: '−', onclick: () => zoomBy(1 / 1.5) }),
      el('button', { 'aria-label': 'Whole map', text: '⤢', onclick: () => { S.view = { k: 1, x: 0, y: 0 }; applyView(); } })));
  const view = { root, stage, marks, points, where, name, size: side, half };
  wire(view);
  return view;
}

function renderViews(main, other) {
  const box = $('views');
  S.views = [buildView(main)].concat(other ? [buildView(other)] : []);
  box.classList.toggle('two', S.views.length === 2);
  $('overview').classList.toggle('two', S.views.length === 2);
  box.replaceChildren(...S.views.map((v) => v.root));
  S.view = { k: 1, x: 0, y: 0 };
  applyLayers();
  applyView();
}

function applyLayers() {
  for (const view of S.views) {
    for (const key of ['rails', 'roads', 'rivers', 'power']) for (const group of view.stage.querySelectorAll('.l-' + key)) group.style.display = S.layers[key] ? '' : 'none';
    view.marks.style.display = S.layers.monuments ? '' : 'none';
  }
}

let queued = false;
function applyView() {
  if (queued) return;
  queued = true;
  requestAnimationFrame(() => {
    queued = false;
    for (const view of S.views) {
      const side = view.root.clientWidth;
      const { k } = S.view;
      // The same view on every map: the offset is kept as a share of the frame.
      S.view.x = Math.min(0, Math.max(1 - k, S.view.x));
      S.view.y = Math.min(0, Math.max(1 - k, S.view.y));
      const x = S.view.x * side;
      const y = S.view.y * side;
      view.stage.style.transform = `translate(${x}px, ${y}px) scale(${k})`;
      // Labels keep clear of the map's name and the zoom buttons.
      const taken = [[0, 0, view.name.offsetWidth + 18, 34], [side - 112, 0, side, 44]];
      for (const point of view.points) {
        const px = x + point.fx * side * k;
        const py = y + point.fy * side * k;
        const inside = px > 0 && py > 0 && px < side && py < side;
        point.dot.style.left = px + 'px';
        point.dot.style.top = py + 'px';
        point.dot.style.display = inside ? '' : 'none';
        // A label stays whole inside the frame: pushed in from the sides, and above its dot at the bottom edge.
        const lx = Math.min(side - point.width / 2 - 2, Math.max(point.width / 2 + 2, px));
        const ly = py + 24 > side ? py - 26 : py;
        point.label.style.left = lx + 'px';
        point.label.style.top = ly + 'px';
        const box = [lx - point.width / 2, ly + 5, lx + point.width / 2, ly + 22];
        const free = inside && !taken.some((t) => box[0] < t[2] && box[2] > t[0] && box[1] < t[3] && box[3] > t[1]);
        point.label.style.display = free ? '' : 'none';
        if (free) taken.push(box);
      }
    }
  });
}

function zoomAt(view, factor, cx, cy) {
  const side = view.root.clientWidth;
  const k = Math.min(12, Math.max(1, S.view.k * factor));
  const ratio = k / S.view.k;
  S.view = { k, x: (cx - (cx - S.view.x * side) * ratio) / side, y: (cy - (cy - S.view.y * side) * ratio) / side };
  if (k === 1) S.view = { k: 1, x: 0, y: 0 };
  applyView();
}
function zoomBy(factor) {
  const view = S.views[0];
  if (view) zoomAt(view, factor, view.root.clientWidth / 2, view.root.clientWidth / 2);
}

function wire(view) {
  const pointers = new Map();
  let pinch = 0;
  const local = (e) => { const r = view.root.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
  view.root.addEventListener('wheel', (e) => { e.preventDefault(); const [cx, cy] = local(e); zoomAt(view, e.deltaY < 0 ? 1.25 : 0.8, cx, cy); }, { passive: false });
  view.root.addEventListener('pointerdown', (e) => {
    if (e.target.closest('.zoom')) return;
    view.root.setPointerCapture(e.pointerId);
    pointers.set(e.pointerId, local(e));
    view.root.classList.add('drag');
  });
  view.root.addEventListener('pointermove', (e) => {
    const [cx, cy] = local(e);
    const side = view.root.clientWidth;
    const wx = ((cx - S.view.x * side) / (side * S.view.k)) * view.size - view.half;
    const wz = view.half - ((cy - S.view.y * side) / (side * S.view.k)) * view.size;
    view.where.textContent = Math.abs(wx) <= view.half && Math.abs(wz) <= view.half ? `x ${Math.round(wx)}, z ${Math.round(wz)}` : '';
    if (!pointers.has(e.pointerId)) return;
    const before = pointers.get(e.pointerId);
    pointers.set(e.pointerId, [cx, cy]);
    if (pointers.size === 1) {
      S.view = { k: S.view.k, x: S.view.x + (cx - before[0]) / side, y: S.view.y + (cy - before[1]) / side };
      applyView();
    } else if (pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      const distance = Math.hypot(a[0] - b[0], a[1] - b[1]);
      if (pinch) zoomAt(view, distance / pinch, (a[0] + b[0]) / 2, (a[1] + b[1]) / 2);
      pinch = distance;
    }
  });
  const end = (e) => { pointers.delete(e.pointerId); pinch = 0; if (!pointers.size) view.root.classList.remove('drag'); };
  view.root.addEventListener('pointerup', end);
  view.root.addEventListener('pointercancel', end);
  view.root.addEventListener('pointerleave', () => { view.where.textContent = ''; });
}
window.addEventListener('resize', applyView);

// ---------- details under the map: one tab at a time, the whole width ----------
const DETAILS = [['checks', 'Checks'], ['numbers', 'Numbers'], ['monuments', 'Monuments'], ['scenery', 'Scenery'], ['templates', 'Templates'],
  ['settings', 'Settings'], ['decided', 'Game decisions'], ['run', 'Run']];
try { S.detail = localStorage.getItem('rustmapgen-detail') || 'checks'; } catch { S.detail = 'checks'; }

function panel(title, hint, body, id) {
  return el('section', { class: 'panel pane', 'data-panel': title, 'data-pane': id || null },
    el('div', { class: 'panel-head' }, el('h2', { text: title }), hint ? el('span', { class: 'hint', text: hint }) : null), body);
}
function table(heads, rows) {
  return el('div', { class: 'table-wrap' }, el('table', { class: 'grid' },
    el('thead', {}, el('tr', {}, heads.map(([text, right, , cls]) => el('th', { class: (right ? 'r' : '') + (cls ? ' ' + cls : ''), text })))),
    el('tbody', {}, rows.map((cells) => el('tr', {}, cells.map((cell, i) => {
      const td = el('td', { class: (heads[i][1] ? 'r' : '') + (i === 0 ? ' k' : '') + (heads[i][2] ? ' long' : '') + (heads[i][3] ? ' ' + heads[i][3] : '') });
      td.append(cell == null ? '–' : cell);
      return td;
    }))))));
}
// A list of label and value as cells of one width that wrap: every cell as tall as the next, so no gaps.
function cells(items, other) {
  return el('div', { class: 'cells' + (other ? ' two' : '') }, items.map(([label, value, otherValue, cls]) => el('div', { class: 'cell' + (cls ? ' ' + cls : '') },
    el('span', { class: 'cl', text: label, title: label }), el('span', { class: 'cv' }, value), other ? el('span', { class: 'cv other' }, otherValue ?? '–') : null)));
}
const NUMBER_GROUPS = [['Paths', /^(rail|ring|road|river|powerline)/], ['Land', /^(land|beach|low_coast|highest)/], ['Shallows', /^sea_/],
  ['Biomes', /^biome_/], ['Tiers', /^topology_/], ['Other', /./]];

function renderPanels(map, other) {
  const panes = [];
  const failed = map.checks.filter((c) => !c.ok).length;
  const checkGroups = [['structure', 'Can it be played', 'what any map must show'], ['recipe', 'Is it what was asked', map.recipe ? `recipe ${map.recipe}` : 'the settings']];
  panes.push([panel('Checks', failed ? `${failed} of ${map.checks.length} fail` : `all ${map.checks.length} pass`, el('div', { class: 'panel-body' },
    checkGroups.map(([kind, title, hint]) => {
      const list = map.checks.filter((c) => (c.kind || 'recipe') === kind);
      return list.length ? el('div', { class: 'sub' }, el('h3', {}, title, el('span', { class: 'muted', text: ' · ' + hint })),
        el('ul', { class: 'checklist' }, list.map((c) => el('li', { class: c.ok ? 'sev-ok' : 'sev-bad' },
          el('span', { class: 'tag ' + (c.ok ? 'ok' : 'bad'), text: c.ok ? 'ok' : 'differs' }), el('b', { text: c.rule }), el('span', { class: 'found', text: c.detail }))))) : null;
    })), 'checks'), failed ? 'bad' : 'ok', `${map.checks.length - failed}/${map.checks.length}`]);

  const unitOf = (value, unit, digits) => value == null ? '–' : fmt(value, digits) + (unit ? (unit === '%' || unit === '°' ? ' ' : ' ') + unit : '');
  const numberGroups = NUMBER_GROUPS.map(([title, test]) => [title, S.metrics.filter(([key]) => test.test(key) && map.numbers[key] != null)]);
  const used = new Set();
  const numberBody = numberGroups.map(([title, list]) => {
    const rows = list.filter(([key]) => !used.has(key));
    rows.forEach(([key]) => used.add(key));
    return rows.length ? el('div', { class: 'sub' }, el('h3', { text: title }),
      cells(rows.map(([key, label, unit, digits]) => [label, unitOf(map.numbers[key], unit, digits), other ? unitOf(other.numbers[key], unit, digits) : undefined]), !!other)) : null;
  });
  panes.push([panel('Numbers', other ? `this map, then ${other.title.split(' · seed')[0]} · ${other.seed}` : 'measured on the file', el('div', { class: 'panel-body' }, numberBody), 'numbers')]);

  const counts = map.facts.monument_counts;
  const names = new Set(Object.keys(counts));
  if (other) for (const key of Object.keys(other.facts.monument_counts)) names.add(key);
  const labels = new Map();
  for (const source of [map, other].filter(Boolean)) for (const m of source.facts.monuments) labels.set(m.prefab, m.label);
  const tidy = (prefab) => labels.get(prefab) || (E.labels && E.labels.get(prefab)) || prefab.replace(/_/g, ' ');
  const monumentCells = [...names].filter((n) => n !== 'watertreatment_pipe_access_point').sort((a, b) => tidy(a).localeCompare(tidy(b))).map((prefab) => {
    const here = counts[prefab] || 0;
    return [tidy(prefab), here ? fmt(here) : el('span', { class: 'muted', text: '0' }), other ? fmt(other.facts.monument_counts[prefab] || 0) : undefined, here ? '' : 'none'];
  });
  panes.push([panel('Monuments', `${Object.keys(counts).length} kinds on this map` + (other ? ', then the other map' : ''), el('div', { class: 'panel-body' }, cells(monumentCells, !!other)), 'monuments'), '', String(Object.keys(counts).length)]);

  const sceneryCells = Object.entries(map.facts.features).map(([name, value]) => [name.charAt(0).toUpperCase() + name.slice(1), fmt(value), other ? fmt(other.facts.features[name]) : undefined, value ? '' : 'none']);
  panes.push([panel('Scenery', 'what the settings switch on or off', el('div', { class: 'panel-body' }, cells(sceneryCells, !!other)), 'scenery')]);

  if (map.merge && map.merge.length) {
    const dropped = map.merge.some((m) => m.dropped);
    panes.push([panel('Templates', 'merged into the file',
      table([['Template'], ['Monument'], ['Copies', true], ['Prefabs placed', true], ['Dropped', true]], map.merge.map((m) => [
        el('span', { class: 'mono', text: m.template }), m.monument, fmt(m.copies), `${fmt(m.placed)} of ${fmt(m.of)}`,
        m.dropped ? el('span', { class: 'tag warn', text: `${m.dropped} unknown to the game` }) : '0'])), 'templates'), dropped ? 'warn' : '', String(map.merge.length)]);
  }

  if (map.settings) panes.push([settingsPanel(map, tidy)]);
  if (map.census) {
    const placed = map.facts.monument_counts;
    const all = map.census.monuments.filter((m) => m.prefab.includes('/monument/')).map((m) => {
      const prefab = m.prefab.split('/').pop().replace('.prefab', '');
      const here = placed[prefab] || 0;
      return { m, here, name: tidy(prefab), row: [tidy(prefab), m.game_min_world_size ? fmt(m.game_min_world_size) : el('span', { class: 'muted', text: 'any' }),
        m.min_world_size != null ? fmt(m.min_world_size) : el('span', { class: 'muted', text: 'unchanged' }),
        m.priority ? `${(m.game_priority || '').toLowerCase()} → ${m.priority.toLowerCase()}` : el('span', { class: 'muted', text: (m.game_priority || '–').toLowerCase() }),
        m.allowed ? 'yes' : el('span', { class: 'tag plain', text: 'blocked' }),
        here ? fmt(here) : (m.allowed ? el('span', { class: 'tag warn', text: 'not placed' }) : el('span', { class: 'muted', text: '0' }))] };
    });
    const groups = [['Blocked by the settings', 'plain', all.filter((x) => !x.m.allowed).map((x) => x.name)],
      ['Allowed, but the game found no room', 'warn', all.filter((x) => x.m.allowed && !x.here).map((x) => x.name)],
      ['Placed earlier than the game would', 'ok', all.filter((x) => x.m.priority).map((x) => `${x.name} · ${(x.m.game_priority || '').toLowerCase()} → ${x.m.priority.toLowerCase()}`)],
      ['Allowed on smaller maps than the game would', 'ok', all.filter((x) => x.m.min_world_size != null).map((x) => `${x.name} · ${fmt(x.m.game_min_world_size)} → ${fmt(x.m.min_world_size)}`)]];
    panes.push([panel('Game decisions', `what the game did with each of ${all.length} monuments`, el('div', { class: 'panel-body' },
      groups.filter(([, , list]) => list.length).map(([title, cls, list]) => el('div', { class: 'sub' }, el('h3', {}, title, el('span', { class: 'muted', text: ` · ${list.length}` })),
        el('ul', { class: 'list chips' }, list.map((text) => el('li', {}, el('span', { class: 'tag ' + cls, text })))))),
      el('p', { class: 'panel-foot flush' }, el('button', { class: 'link', text: S.decidedAll ? 'hide the table' : `every monument in a table (${all.length})`, onclick: () => { S.decidedAll = !S.decidedAll; renderPanels(map, other); } })),
      S.decidedAll ? table([['Monument'], ["Game's smallest world", true, false, 'wide-only'], ['Used here', true, false, 'wide-only'], ['Priority'], ['Allowed'], ['Placed', true]], all.map((x) => x.row)) : null), 'decided')]);
  }
  if (map.run) {
    panes.push([panel('Run', map.name, el('div', { class: 'panel-body' }, el('dl', { class: 'kv' },
      el('dt', { text: 'Game' }), el('dd', { text: `build ${map.run.game_build || '?'}` + (map.run.network_isolated ? ', with no network' : '') }),
      el('dt', { text: 'Time' }), el('dd', { text: `${fmt(map.run.wall_seconds)} s, of which ${fmt(map.report && map.report.seconds_to_save)} s until the map was saved` }),
      map.run.cpu_c_peak != null ? el('dt', { text: 'Hottest CPU' }) : null,
      map.run.cpu_c_peak != null ? el('dd', { text: `${map.run.cpu_c_peak} °C` }) : null,
      el('dt', { text: 'File' }), el('dd', { text: `${fmt(map.map_bytes / 1048576, 1)} MB, ${fmt(map.facts.prefabs)} prefabs` }),
      el('dt', { text: 'Loaded by the game' }), el('dd', {}, loadTest(map)))), 'run')]);
  }
  const present = panes.map(([pane]) => pane.dataset.pane);
  if (!present.includes(S.detail)) S.detail = present[0];
  fill($('d-tabs'), DETAILS.filter(([id]) => present.includes(id)).map(([id, label]) => {
    const [, level, badge] = panes.find(([pane]) => pane.dataset.pane === id);
    return el('button', { class: 'd-tab' + (id === S.detail ? ' on' : ''), role: 'tab', 'aria-selected': String(id === S.detail), 'data-pane': id,
      onclick: () => { S.detail = id; try { localStorage.setItem('rustmapgen-detail', id); } catch { /* this visit only */ } renderPanels(map, other); } },
    label, badge ? el('span', { class: 'badge' + (level ? ' ' + level : ''), text: badge }) : null);
  }));
  for (const [pane] of panes) pane.hidden = pane.dataset.pane !== S.detail;
  $('panels').replaceChildren(...panes.map(([pane]) => pane));
}

// What made a map, in words, and the way back into the settings.
function settingsPanel(map, tidy) {
  const s = map.settings;
  const states = Object.entries(s.monuments || {});
  const chips = (list, cls) => list.length ? el('ul', { class: 'list' }, list.map((text) => el('li', {}, el('span', { class: 'tag ' + cls, text })))) : el('span', { class: 'muted', text: 'none' });
  const land = map.census && map.census.steps.find((step) => step.step === 'Land');
  const t = s.terrain || {};
  const landText = [];
  if (land) landText.push(`land ${fmt(land.game_land_share * 100, 1)} % of the square as the game made it, ${fmt(land.land_share * 100, 1)} % after (shore moved ${fmt(land.coast_lift_m, 1)} m)`);
  if (t.profile) landText.push(`heights bent to ${Object.values(t.profile).map((v) => fmt(v, 1)).join(' / ')} m` + (land && land.profile_made && land.profile_made.length ? ` (the game had ${land.profile_made.map((v) => fmt(v, 1)).join(' / ')} m)` : ''));
  else if (t.height_scale != null && t.height_scale !== 1) landText.push(`heights times ${t.height_scale}`);
  if (t.relief_radius_m) landText.push(`ridges and slopes narrower than ${t.relief_radius_m} m kept at ${t.relief_keep ?? 1}`);
  if (t.shallows) landText.push(`shallows shaped: ${t.shallows.map(([depth, share]) => `${fmt(share * 100, 1)} % to ${depth} m`).join(', ')}`);
  const off = Object.entries(s.world || {}).filter(([, value]) => value === false).map(([key]) => key);
  const scenery = Object.entries(s.scenery || {});
  const stepRules = Object.entries(s.steps || {});
  return panel('Settings', `what made this map${s.name ? ` · ${s.name}` : ''}`, el('div', { class: 'panel-body' },
    el('dl', { class: 'kv' },
      el('dt', { text: 'Prefer' }), el('dd', {}, chips(states.filter(([, v]) => v === 'want').map(([k]) => tidy(k)), 'ok')),
      el('dt', { text: 'Never' }), el('dd', {}, chips(states.filter(([, v]) => v === 'block').map(([k]) => tidy(k)).concat(scenery.filter(([, v]) => v === 'block').map(([k]) => k)), 'bad')),
      el('dt', { text: 'Rails and roads' }), el('dd', { text: `rail ring ${s.rail_ring}, road ring ${s.road_ring}` + (s.build_on_roads ? '; building on roads allowed' : '') + (off.length ? `; switched off: ${off.join(', ')}` : '') + (s.relax_distances !== 1 ? `; distances × ${s.relax_distances}` : '') }),
      el('dt', { text: 'Land' }), el('dd', { text: landText.join('; ') || 'as the game makes it' }),
      s.centre && s.centre.monument ? el('dt', { text: 'In the middle' }) : null,
      s.centre && s.centre.monument ? el('dd', { text: `${tidy(s.centre.monument)}, searched from ${s.centre.start_m} m out, ${s.centre.grow_m} m wider with every attempt` }) : null,
      s.underwater_labs ? el('dt', { text: 'Underwater labs' }) : null,
      s.underwater_labs ? el('dd', { text: `${s.underwater_labs.min} to ${s.underwater_labs.max}, picked by the seed` }) : null,
      stepRules.length ? el('dt', { text: 'Placement steps' }) : null,
      stepRules.length ? el('dd', { class: 'mono', text: stepRules.map(([folder, rule]) => `${folder.replace(/monument\//g, '')}: ${Object.entries(rule).map(([k, v]) => `${k} ${v}`).join(', ')}`).join('; ') }) : null,
      el('dt', { text: 'Templates' }), el('dd', { class: 'mono', text: (s.templates || []).join(', ') || 'none' })),
    el('p', {}, el('button', { class: 'btn', 'data-testid': 'edit-copy', text: 'Edit a copy of these settings', onclick: () => { showEditor(); if (leaveChanges('the settings of this map')) openSettings(s, map.name); } }))), 'settings');
}

// Whether the game itself has loaded final.map, and what it found in it.
function loadTest(map) {
  const test = map.load_test;
  if (!test) return el('span', { class: 'tag plain', text: 'not tested' });
  if (test.state === 'stale') return el('span', { class: 'tag warn', text: 'test again' });
  const ok = test.state === 'done' && test.loaded;
  const troubled = ok && ((test.log_trouble && test.log_trouble.length) || test.loaded.prefabs !== map.facts.prefabs);
  const parts = [el('span', { class: 'tag ' + (ok ? (troubled ? 'warn' : 'ok') : 'bad'), text: ok ? (troubled ? 'yes, with problems' : 'yes') : 'no' })];
  if (ok) {
    const same = test.loaded.prefabs === map.facts.prefabs;
    parts.push(` ${fmt(test.loaded.prefabs)} prefabs${same ? ', all of the file' : ` (the file has ${fmt(map.facts.prefabs)})`}, ${fmt(test.loaded.monuments)} monuments, ${fmt(test.loaded.entities)} entities spawned, in ${fmt(test.seconds)} s on build ${test.game_build}`);
  }
  if (test.log_trouble && test.log_trouble.length) parts.push(el('div', { class: 'mono', text: test.log_trouble.join(' · ') }));
  return parts;
}

// ---------- generator ----------
const clock = (seconds) => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;
// Terminal colours ("\x1b[0m") from logs written before the runner took them out, and the game's own timer.
const cleanStep = (step) => (step || '').replace(/\x1b\[[0-9;?]*[A-Za-z]/g, '').replace(/^\[[^\]]*\]\s*/, '');

// The live parts are drawn again at every read of the status; an animation drawn again starts at the
// clock's own phase, so that it runs on unbroken (style for an element whose animation lasts `ms`).
const inStep = (ms) => `--sync: -${Date.now() % ms}ms`;

function renderStatus(status) {
  S.status = status;
  if (status.version) S.version = status.version;
  S.statusAt = Date.now();
  const box = $('top-status');
  box.className = 'top-status';
  const waiting = status.queue && status.queue.length ? ` · ${status.queue.length} waiting` : '';
  if (status.running) {
    box.classList.add('busy');
    const job = status.job || {};
    const what = job.kind === 'load-test' ? `Testing ${job.name}` : job.kind === 'update' ? 'Updating the game'
      : `Making ${recipeTitle(job.recipe)}${job.seed ? ' · seed ' + job.seed : ''}`;
    box.textContent = `${what} · ${clock(status.seconds)}${status.step ? ' · ' + cleanStep(status.step) : ''}${waiting}`;
  } else if (status.last && !status.last.ok) {
    box.classList.add('bad');
    box.textContent = 'Last run failed: ' + (status.last.tail[status.last.tail.length - 1] || 'no output');
  } else {
    box.textContent = status.last ? `Last run took ${clock(status.last.seconds)}` : 'Idle';
  }
  $('tab-maps').classList.toggle('busy', !!status.running || !!(status.queue && status.queue.length));
  renderLive();
  if (S.current === LIVE) renderLiveView();
  queueSettled(status);
  renderUpdates();
  clearInterval(S.ticker);
  if (status.running) S.ticker = setInterval(tick, 1000);
  // A run finished: the list is read again, and the map shown when it is new.
  const finished = status.last && `${status.last.name}:${status.last.finished || status.last.seconds}`;
  if (finished && S.lastFinished !== undefined && S.lastFinished !== finished) {
    S.cache.clear();
    const next = status.last.ok && status.last.name && status.last.job && status.last.job.kind === 'generate' ? status.last.name : S.current;
    load(next, true).catch(() => { /* the next refresh reads the library */ });
  }
  S.lastFinished = finished || null;
  scheduleStatusPoll(status.running ? 4000 : 15000);
}

// ---------- maps being made ----------
function recipeTitle(name) {
  const base = String(name || '').replace(/-edit$/, '');
  const found = (S.recipes || []).find((x) => x.name === base);
  return ((found && found.title) || base || 'a map') + (base !== String(name || '') ? ' (edited)' : '');
}
// How long a map of this size took here before: the middle of the earlier runs.
function typical(size) {
  const runs = S.maps.filter((m) => m.size === size && m.seconds > 0).map((m) => m.seconds).sort((a, b) => a - b);
  return runs.length ? runs[Math.floor(runs.length / 2)] : null;
}
function failureKey(last) { return `${last.name || ''}:${last.seconds}:${last.tail.length ? last.tail[last.tail.length - 1] : ''}`; }
function failureDismissed(last) { try { return localStorage.getItem('rustmapgen-dismissed') === failureKey(last); } catch { return false; } }

// What is going on: the map being made, the ones waiting, and the last run if it failed.
function liveItems(status) {
  if (!status) return [];
  const items = [];
  if (status.running) items.push({ kind: 'running', job: status.job || {} });
  for (const job of status.queue || []) items.push({ kind: 'waiting', job });
  if (!status.running && status.last && !status.last.ok && !failureDismissed(status.last)) items.push({ kind: 'failed', job: status.last.job || {} });
  return items;
}
function liveTitle(item) {
  const job = item.job;
  if (job.kind === 'update') return 'Updating the game';
  if (job.kind === 'load-test') return 'Game test of ' + ((S.maps.find((m) => m.name === job.name) || {}).title || 'a map').split(' · seed')[0];
  return recipeTitle(job.recipe);
}
function elapsed() { return S.status && S.status.running ? S.status.seconds + (Date.now() - S.statusAt) / 1000 : 0; }
function progress() {
  const job = (S.status && S.status.job) || {};
  const expect = job.kind === 'generate' ? typical(job.size) : null;
  const seconds = elapsed();
  if (!expect) return { share: null, text: '' };
  return { share: Math.min(0.97, seconds / expect), text: seconds < expect - 5 ? `about ${clock(expect - seconds)} left` : 'nearly done' };
}

function renderLive() {
  const items = liveItems(S.status);
  const box = $('live');
  box.replaceChildren(...items.map((item) => {
    const job = item.job;
    const where = job.kind === 'load-test' ? 'loads the finished file' : job.kind === 'update' ? 'from Steam'
      : `${fmt(job.size)} · ${job.seed ? 'seed ' + job.seed : 'random seed'}`;
    const sub = item.kind === 'running' ? el('span', { class: 's' }, el('span', { class: 'clock', text: clock(elapsed()) }), ` · ${cleanStep(S.status.step) || 'starting the game'}`)
      : el('span', { class: 's', text: item.kind === 'waiting' ? `${where} · waiting` : 'see why' });
    const p = item.kind === 'running' ? progress() : null;
    return el('button', { class: `item live ${item.kind}` + (S.current === LIVE ? ' on' : ''), 'data-testid': 'live-' + item.kind, onclick: () => { showMaps(); showLive(); } },
      el('span', { class: 'live-icon', 'aria-hidden': 'true', style: inStep(1000) }),
      el('span', {}, el('span', { class: 't', text: liveTitle(item) }), sub),
      el('span', { class: 'tag ' + { running: 'run', waiting: 'plain', failed: 'bad' }[item.kind], text: { running: 'making', waiting: 'waiting', failed: 'failed' }[item.kind] }),
      p && p.share != null ? el('span', { class: 'bar' }, el('i', { style: `transform: scaleX(${p.share})` })) : null);
  }));
  const strip = $('live-strip');
  const first = items[0];
  strip.hidden = !first || S.current === LIVE;
  if (first) {
    strip.className = 'live-strip ' + first.kind;
    fill(strip, el('i', { style: inStep(1600) }), el('span', { text: { running: 'Making ', waiting: 'Waiting: ', failed: 'Failed: ' }[first.kind] + liveTitle(first) }),
      first.kind === 'running' ? el('span', { class: 'clock', text: clock(elapsed()) }) : null, el('b', { text: 'Show' }));
  }
}

// The map being made, in the place of a map: what it is, how far it has come, and what waits behind it.
function showLive() {
  S.current = LIVE;
  history.replaceState(null, '', '#making');
  for (const item of document.querySelectorAll('.item')) item.classList.toggle('on', item.classList.contains('live'));
  S.views = [];
  renderLive();
  renderLiveView();
}

function renderLiveView() {
  const status = S.status || {};
  const items = liveItems(status);
  const running = items.find((item) => item.kind === 'running');
  const waiting = items.filter((item) => item.kind === 'waiting');
  const failed = items.find((item) => item.kind === 'failed');
  const head = [];
  if (running) {
    const job = running.job;
    const p = progress();
    head.push(el('h1', { text: (job.kind === 'generate' ? 'Making ' : '') + liveTitle(running) }),
      el('div', { class: 'facts' }, job.kind === 'generate' ? [el('span', {}, 'Size ', el('b', { text: fmt(job.size) })), el('span', {}, 'Seed ', el('b', { text: job.seed ? String(job.seed) : 'random' }))] : null,
        el('span', {}, 'Running for ', el('b', { class: 'clock', text: clock(elapsed()) })), p.text ? el('span', { class: 'eta', text: p.text }) : null),
      el('div', { class: 'progress' + (p.share == null ? ' unknown' : '') }, el('i', { style: p.share == null ? inStep(1800) : `transform: scaleX(${p.share})` })),
      el('div', { class: 'note' }, 'The game is at: ', el('b', { text: cleanStep(status.step) || 'starting' }),
        job.note ? el('span', { class: 'muted', text: ` · ${job.note}` }) : null),
      el('div', { class: 'note muted', text: job.kind === 'load-test' ? 'The result appears on the map\'s page.'
        : job.kind === 'update' ? 'steamcmd brings the game to Steam\'s newest public build; maps wait until it is done. On Windows its progress shows only when it ends.'
          : 'When it is done the map opens here by itself, with its checks. You can leave this page and come back.' }));
  } else if (failed) {
    const last = status.last;
    head.push(el('h1', { text: 'The last map was not made' }),
      el('div', { class: 'facts' }, el('span', {}, 'Settings ', el('b', { text: liveTitle(failed) })), el('span', {}, 'Stopped after ', el('b', { text: clock(last.seconds) }))),
      el('pre', { class: 'mono tail', text: last.tail.join('\n') || 'no output' }),
      el('div', { class: 'tools' }, el('button', { class: 'btn', text: 'Dismiss', onclick: () => { try { localStorage.setItem('rustmapgen-dismissed', failureKey(last)); } catch { /* shown until the next run */ } load(null, true); } })));
  } else if (!waiting.length) {
    head.push(el('div', { class: 'empty', text: 'Nothing is being made right now. Start one with "New Map".' }));
  }
  if (waiting.length) {
    head.push(el('div', { class: 'waiting' }, el('h2', { text: `Waiting · ${waiting.length}` }),
      el('ol', {}, waiting.map((item) => el('li', {}, el('b', { text: liveTitle(item) }), ' ', el('span', { class: 'muted', text: item.job.kind === 'load-test' ? 'game test'
        : item.job.kind === 'update' ? 'from Steam' : `${fmt(item.job.size)} · ${item.job.seed ? 'seed ' + item.job.seed : 'random seed'}` })))),
      el('button', { class: 'btn danger', 'data-testid': 'cancel-waiting', text: waiting.length === 1 ? 'Cancel the waiting map' : `Cancel all ${waiting.length} waiting`, onclick: cancelWaiting })));
  }
  $('head').replaceChildren(...head);
  $('views').classList.remove('two');
  $('overview').classList.remove('two');
  for (const id of ['glance', 'd-tabs']) $(id).replaceChildren();
  $('views').replaceChildren(running && running.job.kind === 'generate'
    ? el('div', { class: 'view pending', 'aria-hidden': 'true' }, el('span', { class: 'ring', style: inStep(1100) }), el('span', { class: 'pending-text', text: 'The game is making this map' }))
    : '');
  $('panels').replaceChildren();
}

async function cancelWaiting() {
  if (!confirm('Take every waiting map off the queue? The map being made now carries on.')) return;
  try {
    await api('/api/queue', { method: 'DELETE', headers: SEND });
    renderStatus(await api('/api/status'));
  } catch (error) { alert(error.message); }
}

// Once a second while a map is being made: its clock and its bar.
function tick() {
  if (!S.status || !S.status.running) return clearInterval(S.ticker);
  const now = clock(elapsed());
  for (const node of document.querySelectorAll('.clock')) node.textContent = now;
  const p = progress();
  if (p.share != null) for (const bar of document.querySelectorAll('.item.running .bar i, .progress i')) bar.style.transform = `scaleX(${p.share})`;
  const eta = document.querySelector('.head .eta');
  if (eta) eta.textContent = p.text;
}

// ---------- updates: RustMapGen's own, and the game's ----------
const U = { open: false, app: null, game: null, busy: '' };

function renderUpdates() {
  const box = $('updates');
  const i = S.install || {};
  const a = U.app;
  const g = U.game;
  const steam = g && g.steam && g.steam.public;
  const gameNewer = !!steam && !!i.build && Number(steam) > Number(i.build);
  const updating = !!S.status && ((S.status.job || {}).kind === 'update' || (S.status.queue || []).some((job) => job.kind === 'update'));
  $('updates-btn').classList.toggle('new', !!(a && a.newer && a.how !== 'development') || gameNewer);
  $('updates-btn').setAttribute('aria-expanded', String(U.open));
  box.hidden = !U.open;
  if (!U.open) return;
  const note = (text, kind) => text ? el('p', { class: 'u-note' + (kind ? ' ' + kind : ''), text }) : null;
  let appNote = null;
  if (U.busy === 'app-check') appNote = note('Asking for the newest release…');
  else if (U.busy === 'app-update') appNote = note('Updating…');
  else if (a && a.error) appNote = note(a.error, 'bad');
  else if (a && a.done) appNote = note(a.done, 'good');
  else if (a && a.how === 'development') appNote = note('A development copy (a git clone of another repository): update it with git.');
  else if (a && a.unfinished && a.newer) appNote = note(`The update to ${a.unfinished} did not finish: finish it now.`, 'bad');
  else if (a && a.latest == null) appNote = note('No release has been published yet.');
  else if (a && a.newer) appNote = el('p', { class: 'u-note' }, `${a.latest} is out. `, el('a', { href: a.notes, target: '_blank', rel: 'noopener', text: 'What is new' }));
  else if (a) appNote = note('This is the newest release.', 'good');
  let gameNote = null;
  if (updating) gameNote = note('The game is being updated: see Maps.');
  else if (U.busy === 'game-check') gameNote = note('Asking Steam (about 10 seconds)…');
  else if (g && g.error) gameNote = note(g.error, 'bad');
  else if (steam) gameNote = note((gameNewer ? `Steam has build ${steam}.` : 'Steam has no newer build.') + (g.steam.staging ? ` Staging: build ${g.steam.staging}.` : ''), gameNewer ? '' : 'good');
  fill(box,
    el('div', { class: 'u-row' },
      el('span', { class: 'u-head' }, el('b', { text: 'RustMapGen' }), ` ${S.version || ''}`),
      el('span', { class: 'u-tools' }, a && a.newer && a.how !== 'development' && !a.done
        ? el('button', { class: 'btn primary', type: 'button', 'data-testid': 'app-update', disabled: !!U.busy, onclick: updateApp,
          text: a.unfinished ? `Finish the update to ${a.latest}` : `Update to ${a.latest}` })
        : el('button', { class: 'btn', type: 'button', 'data-testid': 'app-check', disabled: !!U.busy, onclick: checkApp, text: 'Check for updates' })),
      appNote),
    el('div', { class: 'u-row' },
      el('span', { class: 'u-head' }, el('b', { text: 'Game' }), i.installed ? ` build ${i.build || '?'}${i.branch && i.branch !== 'public' ? ` (${i.branch})` : ''}` : ' not installed'),
      el('span', { class: 'u-tools' }, gameNewer && !updating
        ? el('button', { class: 'btn primary', type: 'button', 'data-testid': 'game-update', disabled: !!U.busy, onclick: updateGame, text: 'Update the game' })
        : el('button', { class: 'btn', type: 'button', 'data-testid': 'game-check', disabled: !!U.busy || updating, onclick: checkGame, text: 'Check' })),
      gameNote));
}

async function updateStep(busy, work) {
  U.busy = busy;
  renderUpdates();
  try { await work(); } finally { U.busy = ''; renderUpdates(); }
}
const checkApp = () => updateStep('app-check', async () => {
  try { U.app = await api('/api/app-check', { method: 'POST', headers: SEND, body: '{}' }); } catch (error) { U.app = { error: error.message }; }
});
const checkGame = () => updateStep('game-check', async () => {
  try { U.game = await api('/api/game-check', { method: 'POST', headers: SEND, body: '{}' }); } catch (error) { U.game = { error: error.message }; }
});
const updateGame = () => updateStep('game-update', async () => {
  try { renderStatus((await api('/api/game-update', { method: 'POST', headers: SEND, body: '{}' })).generator); } catch (error) { U.game = { error: error.message }; }
});
const updateApp = () => updateStep('app-update', async () => {
  const to = U.app.latest;
  if (!confirm(`Update RustMapGen to ${to}? Your maps, settings and the game stay as they are.`)) return;
  let done;
  try { done = await api('/api/app-update', { method: 'POST', headers: SEND, body: '{}' }); } catch (error) { U.app = Object.assign({}, U.app, { error: error.message }); return; }
  if (done.restart !== 'auto') {
    U.app = Object.assign({}, U.app, { done: `Updated to ${done.version}. Close the panel's window and start it again (rustmapgen.cmd or ./rustmapgen.sh).` });
    return;
  }
  U.app = Object.assign({}, U.app, { done: `Updated to ${done.version}: the panel is starting again…` });
  renderUpdates();
  // The new panel answers with its own version: then this page is the old one, and is loaded again.
  for (let tries = 0; tries < 60; tries++) {
    await new Promise((resolve) => setTimeout(resolve, 1500));
    try { if ((await api('/api/status')).version === done.version) return location.reload(); } catch { /* not up yet */ }
  }
  U.app = Object.assign({}, U.app, { done: null, error: `Updated to ${done.version}, but the panel did not come back: start it again by hand.` });
});

function scheduleStatusPoll(delay) {
  clearTimeout(S.poll);
  S.poll = setTimeout(async () => {
    try { renderStatus(await api('/api/status')); }
    catch { scheduleStatusPoll(15000); } // bounded retry, including fetch/JSON failures
  }, delay);
}

// ---------- the two views ----------
function showMaps() {
  S.tab = 'maps';
  $('list').hidden = false;
  $('e-nav').hidden = true;
  if (S.current === LIVE) renderLiveView();
  $('main').hidden = false;
  $('editor').hidden = true;
  $('tab-maps').classList.add('on');
  $('tab-gen').classList.remove('on');
  applyView();
}
function showEditor() {
  S.tab = 'gen';
  $('list').hidden = true;
  $('e-nav').hidden = false;
  $('main').hidden = true;
  $('editor').hidden = false;
  $('tab-maps').classList.remove('on');
  $('tab-gen').classList.add('on');
  if (!E.recipe && !restoreDraft()) openRecipe((S.recipes.find((r) => r.name === START_PRESET) || S.recipes[0]).name);
}
$('tab-maps').addEventListener('click', showMaps);
$('tab-gen').addEventListener('click', showEditor);
$('pick').addEventListener('change', (e) => show(e.target.value));

$('top-status').addEventListener('click', () => { if (liveItems(S.status).length) { showMaps(); showLive(); } });
$('updates-btn').addEventListener('click', () => { U.open = !U.open; renderUpdates(); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && U.open) { U.open = false; renderUpdates(); } });
// Seen before the click does anything: a button inside the panel is drawn again by its own click.
document.addEventListener('click', (e) => {
  if (U.open && !e.target.closest('#updates, #updates-btn')) { U.open = false; renderUpdates(); }
}, true);
$('live-strip').addEventListener('click', showLive);

Promise.all([loadRecipes(), load(START_HASH === '#making' ? LIVE : null)]).then(() => {
  if ((S.maps.length || S.current === LIVE) && START_HASH) showMaps(); else showEditor();
}).catch((error) => { $('e-sections').replaceChildren(el('div', { class: 'empty', text: 'The panel could not read its data: ' + error.message })); });
