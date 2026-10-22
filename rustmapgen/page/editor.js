// The settings of new maps: quick choices on top, then every setting of a recipe. Works on a copy;
// "Make maps" sends the copy as it stands, "Save as" keeps it under a name of its own.
// The copy is kept in this browser as it is edited, so a reload or a new tab finds it again.
// Uses the helpers of app.js (el, fmt, api, $, S, SEND).
'use strict';

// baseRecipe: the settings the copy started from, to tell what was changed.
const E = { data: null, recipe: null, baseRecipe: null, base: '', from: null, labels: new Map(), section: 'basics', help: true, showAll: false };
const DRAFT = 'rustmapgen-draft';
// The settings a first visit opens, first among the presets; the others follow by size.
const START_PRESET = 'game-default';
const byStart = (a, b) => (a.name !== START_PRESET) - (b.name !== START_PRESET) || a.size - b.size;
// The summary beside the settings stays clear of the bar, which grows by the line of changes.
if (window.ResizeObserver) new ResizeObserver(([entry]) => document.documentElement.style.setProperty('--bar-h', `${Math.ceil(entry.target.offsetHeight)}px`)).observe(document.getElementById('e-bar'));
try { E.section = localStorage.getItem('rustmapgen-section') || 'basics'; E.help = localStorage.getItem('rustmapgen-help') !== '0'; } catch { /* defaults for this visit */ }
// The settings, one section at a time: the first three are all most maps need.
const NAV = [['Start here', [['basics', 'Basics'], ['monuments', 'Monuments'], ['prefabs', 'Custom prefabs']]],
  ['In detail', [['land', 'Land'], ['roads', 'World features'], ['tiers', 'Tiers and biomes'], ['scenery', 'Scenery'], ['middle', 'Outpost'],
    ['steps', 'Placement steps'], ['block', 'Block'], ['file', 'As a file']]]];
const SECTION_IDS = NAV.flatMap(([, list]) => list.map(([id]) => id));
const STATE_LABEL = { want: 'Prefer', any: 'Auto', block: 'Never' };
const STATE_TITLE = { want: 'Placed first, on any map size, with the distance rules relaxed. Not a promise: the game may find no room.',
  any: 'The game decides, as it would by itself.', block: 'Never placed.' };
const STATE_SHORT = { want: 'placed first, on any map size; the game may still find no room', any: 'the game decides', block: 'never placed' };
// The land of the presets: heights at which 10, 50, 90 and 99 % of the land lies below, and the top (m),
// for a large map and a small one; and how much of the square is sea no deeper than a given depth.
const LOWER_HILLS = { p10: 5.8, p50: 18.2, p90: 31.2, p99: 54.0, max: 68.0 };
const LOWER_HILLS_SMALL = { p10: 2.3, p50: 9.0, p90: 20.1, p99: 40.6, max: 60.5 };
const WIDE_SHALLOWS = [[1, 0.048], [3, 0.068], [6, 0.08], [12, 0.097], [20, 0.12], [30, 0.148], [40, 0.191]];
const copy = (value) => JSON.parse(JSON.stringify(value));
// The most a setting may ask, as the server keeps it: more cannot be made, or makes a broken map.
const limit = (key) => ((E.data && E.data.limits) || {})[key] ?? { land_share: 0.7, height_m: 200 }[key];
const RING = { always: 'always', game: 'as the game', never: 'never' };
const STEP_FIELDS = [['count', 'How many', 200], ['min_world_size', 'From map size', 6000], ['distance_same', 'Apart, same kind (m)', 5000], ['distance_other', 'Apart, other kinds (m)', 5000]];

async function loadRecipes() {
  E.data = await api('/api/recipes');
  S.recipes = E.data.recipes;
  E.labels = new Map(E.data.catalog.monuments.map((m) => [m.prefab, m.label]));
  return E.data;
}

// Replaces a node's content only when it reads differently: a button redrawn under the pointer loses its click.
function refill(node, ...kids) {
  const next = el('div', {}, ...kids);
  if (next.innerHTML !== node.innerHTML) fill(node, ...next.childNodes);
}

function touch() {
  if (E.paintBiomes) E.paintBiomes();
  saveDraft();
  renderChanges();
  renderSummary();
  const preview = $('e-preview-text');
  if (preview) preview.textContent = JSON.stringify(E.recipe, null, 1);
}

// ---------- what was changed, and the copy kept in the browser ----------
const plain = (value) => value != null && typeof value === 'object' && !Array.isArray(value);
const stable = (value) => JSON.stringify(value ?? null, (key, x) => (plain(x) ? Object.fromEntries(Object.keys(x).sort().map((k) => [k, x[k]])) : x));
// These are one setting each, compared whole.
const WHOLE = new Set(['terrain.profile', 'terrain.shallows', 'underwater_labs']);

// Every value that differs between two recipes: [path, before, now]. A missing value and null are the same.
function differences(before, now, path = [], out = []) {
  for (const key of new Set([...Object.keys(before || {}), ...Object.keys(now || {})])) {
    if (!path.length && ['name', 'title', 'source', 'saved', 'broken'].includes(key)) continue;
    const at = [...path, key];
    const a = before ? before[key] : undefined;
    const b = now ? now[key] : undefined;
    if ((plain(a) || plain(b)) && !WHOLE.has(at.join('.'))) differences(plain(a) ? a : {}, plain(b) ? b : {}, at, out);
    else if (stable(a) !== stable(b)) out.push([at, a, b]);
  }
  return out;
}

// A change in words: its label, what it was and is, and the sections that show it.
function describeChange([path, was, now]) {
  const [head, key, field] = path;
  const pct = (v) => (v == null ? 'as the game' : `${fmt(v * 100)} %`);
  const metres = (v) => (v == null ? 'none' : `${fmt(v, v % 1 ? 1 : 0)} m`);
  const lines = (v) => (v && v.length ? `${v.length} ${v.length === 1 ? 'line' : 'lines'}` : 'none');
  const state = (v) => STATE_LABEL[v || 'any'];
  const one = (sections, label, show, extra) => ({ key: path.join('.'), sections, label, was: show(was), now: show(now), ...extra });
  const shareSet = (name, label, list) => {
    const show = (world) => list.map(([k]) => fmt((world[k] ?? 0) * 100)).join(' / ') + ' %';
    return { key: name, sections: ['tiers'], label, was: show(E.baseRecipe.world), now: show(E.recipe.world) };
  };
  if (head === 'size') return one([], 'Size', (v) => fmt(v));
  if (head === 'monuments') return one(['monuments'], E.labels.get(key) || key, state, { state: now || 'any' });
  if (head === 'scenery') {
    const kind = E.data.catalog.scenery.find((k) => k.key === key);
    return one(['scenery'], kind ? kind.label : key, state, { state: now || 'any' });
  }
  if (head === 'world') {
    const sw = E.data.switches.find(([k]) => k === key);
    if (sw) return one(['roads'], sw[1], (v) => (v ? 'on' : 'off'));
    if (E.data.tiers.some(([k]) => k === key)) return shareSet('tiers', 'Tiers', E.data.tiers);
    if (E.data.biomes.some(([k]) => k === key)) return shareSet('biomes', 'Biomes', E.data.biomes);
    if (key === E.data.jungle[0]) return one(['tiers'], 'Jungle', pct);
  }
  if (head === 'terrain') {
    const hills = (v) => (!v ? 'as the game' : [LOWER_HILLS, LOWER_HILLS_SMALL].some((p) => stable(p) === stable(v)) ? 'lower' : 'own profile');
    const known = {
      land_share: [['basics', 'land'], 'Land', pct], profile: [['basics', 'land'], 'Hills', hills],
      shallows: [['basics', 'land'], 'Shallows', (v) => (!v ? 'as the game' : stable(v) === stable(WIDE_SHALLOWS) ? 'wide' : 'own depths')],
      height_scale: [['land'], 'Heights', (v) => (v == null ? 'as the game' : `× ${v}`)], coast_lift_m: [['land'], 'Shore moved out', metres],
      relief_radius_m: [['land'], 'Ridges kept', metres], relief_keep: [['land'], 'Ridge strength', (v) => (v == null ? 'as the game' : `× ${v}`)],
      middle_plateau_m: [['land'], 'Level middle', metres], coast_fade_m: [['land'], 'Sea floor fade', metres] }[key];
    if (known) return one(...known);
  }
  if (head === 'centre') {
    const known = { monument: [['basics', 'middle'], 'In the middle', (v) => (v ? E.labels.get(v) || v : 'nothing')],
      any_tier: [['middle'], 'Middle, any tier', (v) => (v ? 'yes' : 'no')], start_m: [['middle'], 'Middle search start', metres],
      grow_m: [['middle'], 'Middle search widens', metres], within_m: [['middle'], 'Counts as middle', metres] }[key];
    if (known) return one(...known);
  }
  if (head === 'steps') {
    const step = E.data.catalog.steps.find((s) => s.folder === key);
    const what = STEP_FIELDS.find(([k]) => k === field);
    return one(['steps'], `${step ? step.label : key}: ${what ? what[1].replace(/ \(m\)$/, '').toLowerCase() : field}`, (v) => (v == null ? 'as the game' : fmt(v)));
  }
  const simple = {
    rail_ring: [['basics', 'roads'], 'Rail ring', (v) => RING[v] || v], road_ring: [['roads'], 'Road ring', (v) => RING[v] || v],
    relax_distances: [['roads'], 'Distances', pct], build_on_roads: [['basics'], 'Building on roads', (v) => (v ? 'allowed' : 'not allowed')],
    underwater_labs: [['roads'], 'Underwater labs, how many', (v) => (v ? `${v.min} to ${v.max}` : 'as the game')],
    templates: [['prefabs'], 'Custom prefabs', (v) => (v && v.length ? v.join(', ') : 'none')], template_skip: [['prefabs'], 'Left out of templates', lines],
    block_extra: [['block'], 'Block', lines] }[head];
  if (simple && path.length === 1) return one(...simple);
  return one(['file'], path.join(' › '), (v) => (v == null ? 'none' : stable(v).slice(0, 40)));
}

function changes() {
  if (!E.recipe || !E.baseRecipe) return [];
  const seen = new Set();
  const order = (change) => (change.sections.length ? SECTION_IDS.indexOf(change.sections[0]) : -1);
  return differences(E.baseRecipe, E.recipe).map(describeChange).filter((change) => !seen.has(change.key) && seen.add(change.key))
    .sort((a, b) => order(a) - order(b));
}

function baseTitle() {
  if (E.from) {
    const map = (S.maps || []).find((m) => m.name === E.from);
    return map && map.title ? `the settings of ${map.title}` : 'the settings of a map';
  }
  const found = E.data.recipes.find((x) => x.name === E.base);
  return (found && found.title) || E.base || 'the settings';
}

// The notice under the bar: what differs from the settings this started from, with Save and Undo.
function renderChanges() {
  const box = $('e-changes');
  if (!box || !E.recipe) return;
  const list = changes();
  const option = $('e-recipe') && $('e-recipe').selectedOptions[0];
  if (option) {
    option.dataset.text = option.dataset.text || option.textContent;
    option.textContent = option.dataset.text + (list.length ? ' · edited' : '');
  }
  for (const [id, hide] of [['e-about', list.length], ['e-save', list.length]]) if ($(id)) $(id).hidden = !!hide;
  box.hidden = !list.length;
  box.classList.toggle('all', E.showAll);
  const savedBase = !E.from && E.data.recipes.some((x) => x.name === E.base && x.saved);
  // Drawn again only when it reads differently: redrawn under the pointer, a button loses its click.
  const sig = stable([list, E.showAll, savedBase, baseTitle()]);
  if (box.dataset.sig !== sig) {
    box.dataset.sig = sig;
    fill(box, !list.length ? null : [
      el('span', { class: 'e-ch-head' }, el('i'), el('span', {}, 'Edited from ', el('b', { text: baseTitle() }), ` · ${list.length} ${list.length === 1 ? 'change' : 'changes'}, not saved`)),
      el('span', { class: 'e-ch-list' },
        list.map((change) => el('button', { type: 'button', class: 'chg' + (change.state ? ' s-' + change.state : ''), 'data-key': change.key,
          title: `was: ${change.was}` + (change.sections.length ? ' (click to open)' : ''), onclick: change.sections.length ? () => openSection(change.sections[0]) : null },
        change.state ? el('i') : null, el('span', { class: 'l', text: change.label }), el('b', { text: change.now }))),
        el('button', { type: 'button', class: 'link more', text: E.showAll ? 'fewer' : 'more', onclick: () => { E.showAll = !E.showAll; renderChanges(); } })),
      el('span', { class: 'e-ch-acts' },
        savedBase ? el('button', { class: 'btn', type: 'button', text: `Save to "${baseTitle()}"`, onclick: saveHere }) : null,
        el('button', { class: 'btn', type: 'button', text: 'Save as…', onclick: saveAs }),
        el('button', { class: 'btn', type: 'button', 'data-testid': 'undo', text: 'Undo changes', onclick: undoChanges }))]);
    fitChanges();
  }
  renderNav();
}

// One line of changes in the bar, as many as fit, then "+N more"; all of them, wrapped, when asked.
function fitChanges() {
  const list = document.querySelector('#e-changes .e-ch-list');
  if (!list) return;
  const chips = [...list.querySelectorAll('.chg')];
  const more = list.querySelector('.more');
  for (const chip of chips) chip.hidden = false;
  if (E.showAll) { more.hidden = chips.length < 2; return; }
  more.hidden = true;
  const room = list.clientWidth;
  const widths = chips.map((chip) => chip.offsetWidth + 6);
  if (widths.reduce((a, b) => a + b, 0) <= room) return;
  more.hidden = false;
  more.textContent = '+00 more';
  let used = more.offsetWidth + 6;
  let hidden = 0;
  chips.forEach((chip, index) => {
    if (hidden || used + widths[index] > room) { chip.hidden = true; hidden += 1; } else used += widths[index];
  });
  more.textContent = hidden === chips.length ? `show the ${hidden} changes` : `+${hidden} more`;
}
if (window.ResizeObserver) new ResizeObserver(() => fitChanges()).observe(document.getElementById('e-bar'));

function saveDraft() {
  if (!E.recipe) return;
  const value = (id) => ($(id) ? $(id).value : '');
  try {
    localStorage.setItem(DRAFT, JSON.stringify({ v: 1, at: Date.now(), base: E.base, from: E.from, baseRecipe: E.baseRecipe, recipe: E.recipe,
      seed: value('e-seed'), count: value('e-count'), note: value('e-note') }));
  } catch { /* kept for this visit only */ }
}

// The copy this browser kept: true when it was opened again.
function restoreDraft() {
  let draft = null;
  try { draft = JSON.parse(localStorage.getItem(DRAFT) || 'null'); } catch { return false; }
  if (!draft || draft.v !== 1 || !plain(draft.recipe) || !plain(draft.recipe.monuments) || !plain(draft.recipe.terrain)) return false;
  // A preset or saved settings are compared with what they are now; a map's settings with what they were.
  const known = !draft.from && E.data.recipes.find((x) => x.name === draft.base && !x.broken);
  const base = known ? copy(known) : draft.baseRecipe;
  if (!plain(base)) return false;
  delete base.saved;
  E.recipe = Object.assign(copy(E.data.defaults), draft.recipe);
  E.baseRecipe = Object.assign(copy(E.data.defaults), base);
  const moved = fitLimits(E.recipe);
  fitLimits(E.baseRecipe);
  Object.assign(E, { base: draft.base || '', from: draft.from || null, showAll: false });
  renderBar();
  for (const [id, key] of [['e-seed', 'seed'], ['e-count', 'count'], ['e-note', 'note']]) if (draft[key]) $(id).value = draft[key];
  renderEditor();
  const count = changes().length;
  if (count) {
    const at = new Date(draft.at || Date.now());
    const today = at.toDateString() === new Date().toDateString();
    say(`Your ${count} unsaved ${count === 1 ? 'change' : 'changes'} from ${today ? '' : at.toLocaleDateString() + ' '}${at.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} are back.${fitted(moved)}`);
  } else if (moved.length) {
    say(fitted(moved).trim());
  }
  return true;
}

// The most land a map of this size may ask (recipe.py, land_share_max): what the shore's 20 m reached on every
// seed seen at that size, give or take the land check's 5 points; never above the overall limit.
function landMax(size) {
  const limits = (E.data && E.data.limits) || {};
  const [[lowSize, low], [highSize, high]] = limits.land_reach || [[2000, 0.45], [4000, 0.70]];
  const reach = low + (high - low) * ((size || 4250) - lowSize) / (highSize - lowSize);
  return Math.floor(Math.min(limit('land_share'), reach + (limits.land_spare ?? 0.05)) * 100 + 1e-9) / 100;
}

// Settings from before the limits (an older map's, a kept copy, a change of size) are brought within them: what moved, in words.
function fitLimits(recipe) {
  const moved = [];
  const t = recipe.terrain || {};
  if (t.land_share != null && t.land_share > landMax(recipe.size)) {
    moved.push(`land ${fmt(t.land_share * 100)} % to ${fmt(landMax(recipe.size) * 100)} % (the most for ${fmt(recipe.size)})`);
    t.land_share = landMax(recipe.size);
  }
  if (t.profile && t.profile.max > limit('height_m')) {
    const factor = limit('height_m') / t.profile.max;
    for (const key of Object.keys(t.profile)) t.profile[key] = Math.round(t.profile[key] * factor * 10) / 10;
    moved.push(`heights scaled to a top of ${fmt(limit('height_m'))} m`);
  }
  if (recipe.centre && recipe.centre.grow_m < 0.1) {
    moved.push('the middle search widens by 0.1 m');
    recipe.centre.grow_m = 0.1;
  }
  // Templates this computer does not have (settings this browser kept from another install, saved ones or a map's from
  // elsewhere): out, and said, or no map could be made with them and nothing on the page would show why.
  if (Array.isArray(E.data.templates) && recipe.templates) {
    const have = new Set(E.data.templates.map((tpl) => tpl.file));
    const gone = recipe.templates.filter((file) => !have.has(file));
    if (gone.length) {
      moved.push(`${gone.length === 1 ? 'a template' : `${gone.length} templates`} not on this computer taken out (${gone.join(', ')}; `
        + 'add it under Custom prefabs to use it)');
      recipe.templates = recipe.templates.filter((file) => have.has(file));
    }
  }
  const around = new Map((E.data.templates || []).filter((tpl) => tpl.prefab).map((tpl) => [tpl.file, tpl.prefab]));
  const seen = new Set();
  const kept = (recipe.templates || []).filter((file) => !around.has(file) || (!seen.has(around.get(file)) && seen.add(around.get(file))));
  if (recipe.templates && kept.length < recipe.templates.length) {
    moved.push(`one template per monument (${recipe.templates.filter((file) => !kept.includes(file)).join(', ')} unticked)`);
    recipe.templates = kept;
  }
  if (recipe.world && recipe.world.BelowGroundRails === false) {
    moved.push('underground rails on');
    recipe.world.BelowGroundRails = true;
  }
  return moved;
}
const fitted = (moved) => (moved.length ? ` Changed so a map can be made from them: ${moved.join(', ')}.` : '');

// Before other settings replace the copy: one question when it holds changes.
function leaveChanges(next) {
  const count = changes().length;
  return !count || confirm(`Drop your ${count} unsaved ${count === 1 ? 'change' : 'changes'} to ${baseTitle()} and open ${next}?`);
}

function undoChanges() {
  const count = changes().length;
  if (count && !confirm(`Undo your ${count} ${count === 1 ? 'change' : 'changes'} and go back to ${baseTitle()}?`)) return;
  E.recipe = copy(E.baseRecipe);
  E.showAll = false;
  if ($('e-size')) $('e-size').value = E.recipe.size;
  saveDraft();
  renderEditor();
  say('Back to ' + baseTitle() + '.');
}

// The tiers a monument may stand in, as the game stores them: a name, or bits (1 tier 0, 2 tier 1, 4 tier 2).
function tiers(value) {
  if (/^Tier\d$/.test(value)) return value.replace('Tier', 'tier ');
  const bits = Number(value);
  if (!(bits > 0) || bits >= 7) return '';
  return 'tiers ' + [0, 1, 2].filter((tier) => bits & (1 << tier)).join(' and ');
}

// The placement step a monument is placed in, and how many that step places a map on every seed (0: as many as
// fit; the underwater labs follow their own setting). recipe.py has the same rule.
function stepOf(m) {
  const folder = m.path.replace(/^autospawn\//, '').replace(/\/[^/]*$/, '');
  return E.data.catalog.steps.find((step) => step.folder.split(',').includes(folder)) || null;
}
function stepCount(step) {
  if (step.folder === 'monument/underwater_lab' && E.recipe.underwater_labs) return E.recipe.underwater_labs.min;
  const own = ((E.recipe.steps || {})[step.folder] || {}).count;
  return own != null && own >= 0 ? own : step.game_count;
}
// The steps that place fewer than the settings want of them: [step, places, wanted].
function overWanted() {
  const by = new Map();
  for (const m of E.data.catalog.monuments) {
    const step = E.recipe.monuments[m.prefab] === 'want' ? stepOf(m) : null;
    if (step) by.set(step, (by.get(step) || []).concat([m]));
  }
  return [...by].filter(([step, wanted]) => stepCount(step) > 0 && wanted.length > stepCount(step)).map(([step, wanted]) => [step, stepCount(step), wanted]);
}

// The game's biome names, as a monument's catalog row lists them, and the share that makes each.
const BIOME = { Arid: ['PercentageBiomeArid', 'Desert'], Temperate: ['PercentageBiomeTemperate', 'Temperate'], Tundra: ['PercentageBiomeTundra', 'Tundra'],
  Arctic: ['PercentageBiomeArctic', 'Snow'], Jungle: ['PercentageBiomeJungle', 'Jungle'] };
const onlyIn = (m) => (Array.isArray(m.biomes) && m.biomes.length ? m.biomes.map((name) => (BIOME[name] || [null, name])[1]).join(' or ') : '');
// A monument whose every biome is at 0 % in these settings: the game cannot place it.
const biomeOff = (m) => Array.isArray(m.biomes) && m.biomes.length > 0 && m.biomes.every((name) => BIOME[name] && !(E.recipe.world[BIOME[name][0]] > 0));

// ---------- small controls ----------
function tri(value, onChange, states = ['want', 'any', 'block'], labels = STATE_LABEL, titles = STATE_TITLE) {
  const box = el('div', { class: 'tri', role: 'group' });
  const paint = (now) => { for (const button of box.children) { button.classList.toggle('on', button.dataset.state === now); button.setAttribute('aria-pressed', String(button.dataset.state === now)); } };
  for (const state of states) {
    box.append(el('button', { type: 'button', class: 's-' + state, 'data-state': state, title: (titles && titles[state]) || null,
      onclick: () => { paint(state); onChange(state); touch(); } }, labels === STATE_LABEL ? el('i') : null, labels[state] || state));
  }
  paint(value);
  box.set = paint;
  return box;
}

function number(value, options, onChange) {
  const range = options.min != null && options.max != null ? `${fmt(options.min)} to ${fmt(options.max)}` : null;
  const input = el('input', { type: 'number', class: 'num-in', value: value ?? '', min: options.min, max: options.max, step: options.step ?? 'any',
    placeholder: options.placeholder ?? '', 'aria-label': options.label || '', title: range });
  // Taken while typing, so nothing waits for the box to lose focus; leaving the box takes only what typing did not
  // (an emptied box), since redrawing then could swallow the click that took the focus away.
  // A number out of range is not taken while typing (3,000 passes 3, 30 and 300 on the way): the box shows it
  // red, and leaving the box brings it to the nearest end of the range.
  let taken = input.value;
  const take = (value) => { taken = input.value; onChange(value); touch(); };
  const inRange = (v) => (options.min == null || v >= options.min) && (options.max == null || v <= options.max);
  input.addEventListener('input', () => {
    const v = Number(input.value);
    const usable = input.value !== '' && Number.isFinite(v);
    input.classList.toggle('out', usable && !inRange(v));
    if (usable && inRange(v)) take(v);
  });
  input.addEventListener('change', () => {
    const v = Number(input.value);
    if (input.value !== '' && Number.isFinite(v) && !inRange(v)) input.value = String(Math.min(options.max ?? v, Math.max(options.min ?? v, v)));
    input.classList.remove('out');
    if (input.value !== taken) take(input.value === '' ? null : Number(input.value));
  });
  return input;
}

function toggle(label, value, onChange, hint) {
  const input = el('input', { type: 'checkbox' });
  input.checked = !!value;
  input.addEventListener('change', () => { onChange(input.checked); touch(); });
  return el('label', { class: 'check' }, input, el('span', {}, label, hint ? el('small', { text: hint }) : null));
}

function slider(value, options, onInput, show) {
  const input = el('input', { type: 'range', min: options.min, max: options.max, step: options.step, value, 'aria-label': options.label || '' });
  const out = el('output', { text: show(value) });
  input.addEventListener('input', () => { const v = Number(input.value); out.textContent = show(v); onInput(v); touch(); });
  const box = el('div', { class: 'slide' }, input, out);
  box.set = (v) => { input.value = v; out.textContent = show(v); };
  return box;
}

function section(id, title, hint, ...body) {
  return el('section', { class: 'panel e-sec', 'data-section': title, 'data-id': id },
    el('div', { class: 'panel-head' }, el('h2', { text: title }), hint ? el('span', { class: 'hint', text: hint }) : null),
    el('div', { class: 'panel-body' }, ...body));
}
const row = (label, control, hint) => el('div', { class: 'e-row' }, el('span', { class: 'e-label' }, el('span', {}, label), hint ? el('small', { class: 'e-hint', text: hint }) : null), control);
// A number and its unit, side by side.
const unit = (control, text) => el('span', { class: 'with-unit' }, control, el('span', { class: 'unit', text }));

// Sliders of a set of shares that must add up to 1: moving one spreads the difference over the others.
function shares(keys, world) {
  const boxes = {};
  const total = el('span', { class: 'e-hint' });
  const paint = () => { total.textContent = `total ${fmt(keys.reduce((sum, [key]) => sum + world[key], 0) * 100)} %`; };
  const rows = keys.map(([key, label]) => {
    boxes[key] = slider(Math.round(world[key] * 100), { min: 0, max: 100, step: 1, label }, (value) => {
      const others = keys.map(([k]) => k).filter((k) => k !== key);
      const rest = 100 - value;
      const before = others.reduce((sum, k) => sum + world[k], 0) * 100;
      world[key] = value / 100;
      let left = rest;
      others.forEach((k, index) => {
        const part = index === others.length - 1 ? left : Math.round(before > 0 ? (world[k] * 100 / before) * rest : rest / others.length);
        world[k] = Math.max(0, part) / 100;
        left -= part;
        boxes[k].set(Math.round(world[k] * 100));
      });
      paint();
    }, (v) => `${v} %`);
    return row(label, boxes[key]);
  });
  paint();
  return [...rows, total];
}

// ---------- quick settings ----------
function quickSection(r) {
  const t = r.terrain;
  const hills = t.profile ? 'lower' : (t.height_scale != null && t.height_scale !== 1 ? 'custom' : 'game');
  const about = E.data.recipes.find((x) => x.name === E.base);
  return el('section', { class: 'panel e-sec quick', 'data-section': 'Quick settings', 'data-id': 'basics' },
    el('div', { class: 'panel-head' }, el('h2', { text: 'Basics' }), el('span', { class: 'hint', text: 'the big choices; the rest is under "In detail"' })),
    el('div', { class: 'panel-body' },
      about && about.source && !E.from ? el('p', { id: 'e-about', class: 'e-about', text: about.source, hidden: changes().length > 0 }) : null,
      row('Island', tri(t.land_share != null ? 'share' : 'game', (state) => {
        if (state === 'share') { t.land_share = Math.min(t.land_share ?? 0.62, landMax(r.size)); delete t.coast_lift_m; } else { delete t.land_share; delete t.coast_lift_m; }
        renderEditor();
      }, ['game', 'share'], { game: 'As the game', share: 'Set the size' }, null),
      'the game alone: about half of a large map is land'),
      t.land_share != null ? row('Land, share of the square', slider(Math.round(t.land_share * 100), { min: 25, max: Math.round(landMax(r.size) * 100), step: 1, label: 'Land share' },
        (v) => { t.land_share = v / 100; }, (v) => `${v} %`), `more land, more room for monuments; at most ${fmt(landMax(r.size) * 100)} % on a map of ${fmt(r.size)}`) : null,
      row('Hills', tri(hills, (state) => {
        if (state === 'lower') {
          t.profile = copy(r.size < 2500 ? LOWER_HILLS_SMALL : LOWER_HILLS);
          t.relief_radius_m = t.relief_radius_m || 70;
          t.relief_keep = t.relief_keep ?? 0.85;
          delete t.height_scale;
        } else { delete t.profile; delete t.height_scale; delete t.relief_radius_m; delete t.relief_keep; }
        renderEditor();
      }, ['game', 'lower'], { game: 'As the game', lower: 'Lower' }, null),
      hills === 'custom' ? 'set by hand under Land' : 'Lower: gentler hills that keep their ridges and slopes'),
      row('Shallows', tri(t.shallows ? 'wide' : 'game', (state) => {
        if (state === 'wide') t.shallows = copy(WIDE_SHALLOWS); else delete t.shallows;
        renderEditor();
      }, ['game', 'wide'], { game: 'As the game', wide: 'Wide' }, null), 'Wide: a broad shallow strip along the coast'),
      el('div', { class: 'checks' },
        toggle('Rail ring', r.rail_ring === 'always', (on) => { r.rail_ring = on ? 'always' : 'game'; renderEditor(); }, `the game adds one from size ${fmt(E.data.catalog.rail_ring_game_size)}`),
        toggle('Building on roads', r.build_on_roads, (on) => { r.build_on_roads = on; }, 'roads stay; players may build on them'),
        toggle('Outpost in the middle', r.centre.monument === 'compound', (on) => {
          r.centre.monument = on ? 'compound' : '';
          if (on && r.monuments.compound !== 'want') r.monuments.compound = 'want';
          renderEditor();
        }, 'searched from the middle outwards'))));
}

// ---------- templates ----------
function templatesSection(r) {
  const list = E.data.templates;
  const input = el('input', { type: 'file', accept: '.map', 'aria-label': 'Template file', 'data-testid': 'template-file' });
  const upload = async () => {
    const file = input.files && input.files[0];
    if (!file) return say('Choose a .map file first.', true);
    try {
      const answer = await api('/api/templates/' + encodeURIComponent(file.name), { method: 'PUT', headers: { 'X-RustMapGen': '1', 'Content-Type': 'application/octet-stream' }, body: file });
      await loadRecipes();
      if (!r.templates.includes(answer.file)) r.templates.push(answer.file);
      touch();
      renderEditor();
      say(answer.monument ? `Added ${answer.file}: ${answer.prefabs} prefabs around the ${answer.monument}.` : `Added ${answer.file}. ${answer.note || ''}`);
    } catch (error) { say(error.message, true); }
  };
  const remove = async (file) => {
    if (!confirm(`Delete the template ${file}? Maps made with it keep it.`)) return;
    try {
      await api('/api/templates/' + encodeURIComponent(file), { method: 'DELETE', headers: SEND });
      r.templates = r.templates.filter((name) => name !== file);
      await loadRecipes();
      renderEditor();
    } catch (error) { say(error.message, true); }
  };
  const rows = list.map((tpl) => el('div', { class: 'tpl' },
    (() => { const box = el('input', { type: 'checkbox', 'aria-label': `Use ${tpl.file}` }); box.checked = r.templates.includes(tpl.file);
      box.addEventListener('change', () => {
        // One template per monument: two around the same monument would both be added to every copy of it.
        const rivals = box.checked && tpl.prefab ? list.filter((other) => other.file !== tpl.file && other.prefab === tpl.prefab && r.templates.includes(other.file)).map((other) => other.file) : [];
        r.templates = box.checked ? [...new Set([...r.templates.filter((name) => !rivals.includes(name)), tpl.file])] : r.templates.filter((name) => name !== tpl.file);
        touch();
        if (rivals.length) { renderEditor(); say(`One template per monument: ${rivals.join(', ')} unticked for ${tpl.file}.`); }
      }); return box; })(),
    el('span', { class: 'name' }, tpl.file, el('br'), el('small', { text: tpl.monument ? `${tpl.prefabs} prefabs around the ${tpl.monument}` + (tpl.dropped ? `, ${tpl.dropped} unknown to the game (left out)` : '') : 'checked when the first map is made' })),
    el('button', { class: 'link', text: 'delete', onclick: () => remove(tpl.file) })));
  const missing = r.templates.filter((name) => !list.some((tpl) => tpl.file === name));
  return section('prefabs', 'Custom prefabs', 'your prefabs around a monument, on every copy of it',
    rows.length ? el('div', { class: 'tpl-list' }, rows) : el('p', { class: 'e-note', text: 'No template yet: monuments stay as the game makes them.' }),
    missing.length ? el('p', { class: 'e-note' }, `Not on this computer: ${missing.join(', ')}. Add ${missing.length === 1 ? 'it' : 'them'} again, or `,
      el('button', { type: 'button', class: 'link', text: `take ${missing.length === 1 ? 'it' : 'them'} out of these settings`, onclick: () => {
        r.templates = r.templates.filter((name) => !missing.includes(name)); touch(); renderEditor(); } })) : null,
    el('div', { class: 'upload' }, input, el('button', { class: 'btn', type: 'button', text: 'Add template', onclick: upload })),
    el('p', { class: 'e-note', text: 'A .map from a map editor with one game monument and your prefabs around it. One template per monument can be ticked.' }),
    row('Leave out of templates', lines(r.template_skip, (value) => { r.template_skip = value; }, 'Template prefabs to leave out', 'one per line, e.g. snowmobilespawner'),
      'prefabs whose path contains one of these lines'));
}

function lines(list, onChange, label, placeholder) {
  const area = el('textarea', { rows: 3, 'aria-label': label, placeholder }, (list || []).join('\n'));
  area.addEventListener('input', () => { onChange(area.value.split('\n').map((line) => line.trim()).filter(Boolean)); touch(); });
  return area;
}

// ---------- the editor ----------
function renderEditor() {
  const { catalog } = E.data;
  const r = E.recipe;
  const out = [quickSection(r), templatesSection(r)];

  // Monuments: a card per group; inside, cells of one width that wrap, so no group leaves a gap.
  const groups = [];
  for (const monument of catalog.monuments) {
    let group = groups.find((g) => g.name === monument.group);
    if (!group) { group = { name: monument.group, rows: [] }; groups.push(group); }
    group.rows.push(monument);
  }
  // Each cell's dot, the legend's counts and the section list follow the states.
  const legend = el('div', { class: 'e-legend' });
  const was = (m) => E.baseRecipe.monuments[m.prefab] || 'any';
  const paintCounts = () => {
    const tally = monumentTally();
    const moved = catalog.monuments.filter((m) => (r.monuments[m.prefab] || 'any') !== was(m)).length;
    const stranded = catalog.monuments.filter((m) => biomeOff(m) && r.monuments[m.prefab] !== 'block').length;
    fill(legend, ['want', 'any', 'block'].map((state) => el('span', { class: 'e-key s-' + state },
      el('i'), el('b', { text: STATE_LABEL[state] }), el('span', { class: 'n', text: String(tally[state]) }), STATE_SHORT[state])),
    moved ? el('span', { class: 'e-key changed' }, el('i', { class: 'chg-mark' }), el('b', { text: 'Changed' }), el('span', { class: 'n', text: String(moved) }), `from ${baseTitle()}`) : null,
    stranded ? el('span', { class: 'e-key stranded' }, el('b', { text: 'No room' }), el('span', { class: 'n', text: String(stranded) }), 'their biome is at 0 %') : null,
    overWanted().map(([step, places, wanted]) => el('span', { class: 'e-key stranded', title: 'Raise "How many" of this step under Placement steps to place more.' },
      el('b', { text: 'Too many preferred' }), `${wanted.length} of ${step.label}, the game places ${places} a map`)));
    renderNav();
  };
  // Preferring more of a step than it places: where it places one, the newly preferred takes the place of the one
  // preferred before; where it places more, it is refused. Either way the group card says so.
  const cells = new Map();
  const keepToStep = (m, note) => {
    const step = stepOf(m);
    const places = step ? stepCount(step) : 0;
    const others = places > 0 ? E.data.catalog.monuments.filter((o) => o.prefab !== m.prefab && r.monuments[o.prefab] === 'want' && stepOf(o) === step) : [];
    if (others.length < places || !others.length) { note.hidden = true; return true; }
    const how = 'For more, raise its "How many" under Placement steps.';
    if (places === 1) {
      for (const o of others) { const [set, control] = cells.get(o.prefab); set('any'); control.set('any'); }
      fill(note, `The game places one ${step.label} a map: ${m.label} is preferred now, and ${others.map((o) => o.label).join(', ')} back to Auto. ${how}`);
      note.hidden = false;
      return true;
    }
    fill(note, `The game places ${places} ${step.label} a map, and ${places} are preferred already. ${how}`);
    note.hidden = false;
    return false;
  };
  const groupCards = groups.map((group) => {
    const controls = [];
    const note = el('p', { class: 'e-group-note', 'data-testid': 'step-limit', hidden: true });
    const body = group.rows.map((m) => {
      const hints = [m.game_min_world_size ? `the game alone: from size ${fmt(m.game_min_world_size)}` : '', m.game_priority && !['Default', 'none'].includes(m.game_priority) ? `priority ${m.game_priority.toLowerCase()}` : '',
        tiers(m.tier), onlyIn(m) ? `only in ${onlyIn(m)}` : ''].filter(Boolean).join(' · ');
      const now = r.monuments[m.prefab] || 'any';
      const cell = el('div', { class: 'e-mon' + (now !== was(m) ? ' changed' : ''), 'data-state': now, title: hints || null });
      const set = (state) => { if (state === 'any') delete r.monuments[m.prefab]; else r.monuments[m.prefab] = state; cell.dataset.state = state; cell.classList.toggle('changed', state !== was(m)); paintCounts(); };
      const control = tri(now, (state) => {
        if (state === 'want' && !keepToStep(m, note)) { control.set(r.monuments[m.prefab] || 'any'); return; }
        set(state);
      });
      controls.push([set, control]);
      cells.set(m.prefab, [set, control]);
      cell.append(el('span', { class: 'k' }, el('i', { class: 'dot' }), el('span', { class: 'name', text: m.label }),
        el('i', { class: 'chg-mark', title: `changed: was ${STATE_LABEL[was(m)]}` }),
        biomeOff(m) ? el('span', { class: 'need', text: `${onlyIn(m)} 0 %`, title: `Placed only in ${onlyIn(m)}, which these settings set to 0 % (Tiers and biomes)` }) : null,
        m.game_min_world_size ? el('span', { class: 'size', text: `${fmt(m.game_min_world_size)}+` }) : null), control);
      return cell;
    });
    // "Set all" keeps to the steps' limits too: a step that places one ends with the last of its kinds wanted.
    const all = (state) => {
      note.hidden = true;
      group.rows.forEach((m, index) => { const [set, control] = controls[index]; if (state !== 'want' || keepToStep(m, note)) { set(state); control.set(state); } });
      touch();
    };
    return el('section', { class: 'panel e-group', 'data-group': group.name },
      el('div', { class: 'panel-head' }, el('h2', { text: group.name }), el('span', { class: 'hint', text: String(group.rows.length) }),
        el('span', { class: 'e-all' }, 'set all', ...['want', 'any', 'block'].map((state) => el('button', { type: 'button', class: 'pill s-' + state, title: `${STATE_LABEL[state]}: every one of ${group.name.toLowerCase()}`,
          onclick: () => all(state) }, el('i'), STATE_LABEL[state])))),
      note, el('div', { class: 'e-mons' }, body));
  });
  paintCounts();
  out.push(el('div', { class: 'e-sec plain', 'data-section': 'Monuments', 'data-id': 'monuments' },
    el('section', { class: 'panel' },
      el('div', { class: 'panel-head' }, el('h2', { text: 'Monuments' }), el('span', { class: 'hint', text: 'which monuments the map should have; 3,500+ is the smallest map the game alone puts one on' })),
      legend),
    ...groupCards));

  // Land
  const t = r.terrain;
  const profileKeys = [['p10', 'A tenth of the land below'], ['p50', 'Half of the land below'], ['p90', '90 % of the land below'], ['p99', '99 % of the land below'], ['max', 'Highest point']];
  const landBody = el('div', {});
  const paintLand = () => {
    const mode = t.land_share != null ? 'share' : 'lift';
    landBody.replaceChildren(
      row('Shore', tri(mode, (state) => {
        if (state === 'share') { t.land_share = Math.min(t.land_share ?? 0.62, landMax(r.size)); delete t.coast_lift_m; } else { delete t.land_share; t.coast_lift_m = t.coast_lift_m ?? 0; }
        renderEditor();
      }, ['share', 'lift'], { share: 'By share of land', lift: 'By metres' }, null)),
      mode === 'share'
        ? row('Land, share of the square', slider(Math.round(t.land_share * 100), { min: 20, max: Math.round(landMax(r.size) * 100), step: 1, label: 'Land share (detail)' }, (v) => { t.land_share = v / 100; }, (v) => `${v} %`), `the game alone: about 50 % at 3500, 20 to 27 % at 1600; at most ${fmt(landMax(r.size) * 100)} % on a map of ${fmt(r.size)} (the shore moves 20 m at most)`)
        : row('Shore moved out by', unit(number(t.coast_lift_m ?? 0, { min: -10, max: 20, step: 0.5, label: 'Shore moved by metres' }, (v) => { t.coast_lift_m = v ?? 0; }), 'm'), 'how far the shore moves out; 0 keeps the game\'s own'),
      row('Heights', tri(t.profile ? 'profile' : 'scale', (state) => {
        if (state === 'profile') { t.profile = t.profile || copy(r.size < 2500 ? LOWER_HILLS_SMALL : LOWER_HILLS); delete t.height_scale; } else { delete t.profile; t.height_scale = t.height_scale ?? 1; }
        renderEditor();
      }, ['profile', 'scale'], { profile: 'Bend to a profile', scale: 'Multiply' }, null)),
      ...(t.profile
        ? profileKeys.map(([key, label]) => row(label, unit(number(t.profile[key], { min: 0.5, max: limit('height_m'), step: 0.1, label }, (v) => { if (v != null) t.profile[key] = v; }), 'm')))
        : [row('Every height times', unit(number(t.height_scale ?? 1, { min: 0.2, max: 2, step: 0.05, label: 'Height scale' }, (v) => { t.height_scale = v ?? 1; }), '×'), '1 = the game\'s own hills; 0.65 flattens them')]),
      row('Keep ridges and slopes narrower than', unit(number(t.relief_radius_m ?? 0, { min: 0, max: 400, step: 5, label: 'Relief radius' }, (v) => { t.relief_radius_m = v ?? 0; }), 'm'),
        '0 flattens slopes with the hills; 70 keeps them'),
      row('…at this strength', unit(number(t.relief_keep ?? 1, { min: 0, max: 2, step: 0.05, label: 'Relief strength' }, (v) => { t.relief_keep = v ?? 1; }), '×'), '1 = as the game made them'),
      toggle('Shape the shallows', !!t.shallows, (on) => { if (on) t.shallows = copy(WIDE_SHALLOWS); else delete t.shallows; renderEditor(); },
        'how much of the square is no deeper than each depth'),
      ...(t.shallows || []).map((pair, index) => row(`Sea no deeper than ${pair[0]} m`, unit(number(Math.round(pair[1] * 1000) / 10, { min: 0.1, max: 40, step: 0.1, label: `Share of sea to ${pair[0]} m` },
        (v) => { if (v != null) t.shallows[index][1] = v / 100; }), '%'))),
      row('Level the land around the middle within', unit(number(t.middle_plateau_m ?? 0, { min: 0, max: 600, step: 10, label: 'Middle plateau' }, (v) => { t.middle_plateau_m = v ?? 0; }), 'm'),
        '0 leaves it; about 120 flattens ground for the monument in the middle'),
      row('Sea floor rejoins the game\'s at the depth of', unit(number(t.coast_fade_m ?? 25, { min: 1, max: 60, step: 1, label: 'Fade depth' }, (v) => { t.coast_fade_m = v ?? 25; }), 'm')));
  };
  paintLand();
  out.push(section('land', 'Land', 'the shape of the island, made before anything is placed', landBody));

  // World features: the game's own switches, the labs and how many, the rings
  const ringLabels = { always: 'Always', game: 'As the game', never: 'Never' };
  const labs = r.world.UnderwaterLabs === false ? 'off' : r.underwater_labs ? 'set' : 'game';
  out.push(section('roads', 'World features', 'the game\'s own switches and rings',
    el('div', { class: 'checks' }, E.data.switches.filter(([key]) => key !== 'UnderwaterLabs').map(([key, label]) => toggle(label, r.world[key], (on) => { r.world[key] = on; },
      key === 'Powerlines' ? 'the large pylons' : ''))),
    row('Underwater labs', tri(labs, (state) => {
      r.world.UnderwaterLabs = state !== 'off';
      r.underwater_labs = state === 'set' ? r.underwater_labs || { min: 2, max: 3 } : null;
      renderEditor();
    }, ['off', 'game', 'set'], { off: 'None', game: 'As the game', set: 'How many' }, null), 'the game alone places one'),
    labs === 'set' ? row('Labs, from … to', el('span', { class: 'with-unit' },
      number(r.underwater_labs.min, { min: 0, max: 12, step: 1, label: 'Fewest labs' }, (v) => { r.underwater_labs.min = v ?? 1; }), el('span', { class: 'unit', text: 'to' }),
      number(r.underwater_labs.max, { min: 0, max: 12, step: 1, label: 'Most labs' }, (v) => { r.underwater_labs.max = v ?? 1; })), 'the seed picks a number in the range') : null,
    row('Rail ring', tri(r.rail_ring, (state) => { r.rail_ring = state; }, ['always', 'game', 'never'], ringLabels, null), `the game makes one from size ${fmt(catalog.rail_ring_game_size)}`),
    row('Road ring', tri(r.road_ring, (state) => { r.road_ring = state; }, ['always', 'game', 'never'], ringLabels, null), `the game makes one from size ${fmt(catalog.road_ring_game_size)}`),
    row('Distances between monuments', slider(Math.round(r.relax_distances * 100), { min: 25, max: 200, step: 5, label: 'Distance factor' }, (v) => { r.relax_distances = v / 100; }, (v) => `${v} %`),
      'of the game\'s own; lower packs more monuments in')));

  // Tiers and biomes. The note names the monuments a biome at 0 % keeps off the map, as the sliders move.
  const biomeNote = el('p', { class: 'e-note warn-note' });
  E.paintBiomes = () => {
    const off = catalog.monuments.filter((m) => biomeOff(m) && E.recipe.monuments[m.prefab] !== 'block');
    const wanted = off.filter((m) => E.recipe.monuments[m.prefab] === 'want');
    biomeNote.hidden = !off.length;
    biomeNote.textContent = off.length ? `Not placed with these shares: ${off.map((m) => `${m.label} (${onlyIn(m)})`).join(', ')}.`
      + (wanted.length ? ` ${wanted.length} of them ${wanted.length === 1 ? 'is' : 'are'} set to Prefer.` : '') : '';
  };
  E.paintBiomes();
  out.push(section('tiers', 'Tiers and biomes', 'shares of the land; each set adds up to 100 %',
    el('h3', { text: 'Tiers' }), ...shares(E.data.tiers, r.world),
    el('p', { class: 'e-note', text: 'With little tier 0 the map has few places for players to spawn.' }),
    el('h3', { text: 'Biomes' }), ...shares(E.data.biomes, r.world),
    row(E.data.jungle[1], slider(Math.round(r.world[E.data.jungle[0]] * 100), { min: 0, max: 100, step: 5, label: 'Jungle' }, (v) => { r.world[E.data.jungle[0]] = v / 100; }, (v) => `${v} %`)),
    el('p', { class: 'e-note', text: 'Some monuments only stand in one tier or biome: a small share can keep them off the map.' }),
    biomeNote));

  // Scenery
  out.push(section('scenery', 'Scenery', 'Prefer also puts it on maps smaller than the game would',
    ...catalog.scenery.map((kind) => {
      const states = kind.step ? ['want', 'any', 'block'] : ['any', 'block'];
      return row(kind.label, tri(r.scenery[kind.key] || 'any', (state) => { if (state === 'any') delete r.scenery[kind.key]; else r.scenery[kind.key] = state; }, states),
        kind.game_min_world_size ? `game: from size ${fmt(kind.game_min_world_size)}` : '');
    })));

  // The monument in the middle of the map: the Outpost, unless another is chosen
  out.push(section('middle', 'Outpost', 'the monument in the middle of the map: the Outpost, or another',
    row('Monument in the middle', (() => {
      const select = el('select', { 'aria-label': 'Monument in the middle' }, el('option', { value: '', text: 'none' }),
        catalog.monuments.filter((m) => !(limit('not_in_the_middle') || ['roadside', 'offshore']).includes(m.folder)).map((m) => el('option', { value: m.prefab, text: m.label, selected: r.centre.monument === m.prefab })));
      select.addEventListener('change', () => { r.centre.monument = select.value; touch(); });
      return select;
    })(), 'searched outward from the middle until it fits'),
    toggle('It may stand in any tier', r.centre.any_tier, (on) => { r.centre.any_tier = on; },
      'on: right at the middle; off: as far out as its tier needs, 35 to 600 m'),
    row('Search starts within', unit(number(r.centre.start_m, { min: 0.5, max: 500, step: 0.5, label: 'Search start' }, (v) => { r.centre.start_m = v ?? 4; }), 'm'), 'of the middle'),
    row('Widens by', unit(number(r.centre.grow_m, { min: 0.1, max: 20, step: 0.05, label: 'Search growth' }, (v) => { r.centre.grow_m = v ?? 0.35; }), 'm'), 'with every attempt; 0.35 reaches about 350 m'),
    row('Counts as in the middle within', unit(number(r.centre.within_m, { min: 10, max: 2000, step: 10, label: 'Check distance' }, (v) => { r.centre.within_m = v ?? 600; }), 'm'), 'for the check on the map\'s page')));

  // Placement steps: a row per step on a wide screen, a small card per step on a phone.
  const stepRows = catalog.steps.map((step) => {
    const rule = () => (r.steps[step.folder] = r.steps[step.folder] || {});
    const game = { count: step.game_count, min_world_size: step.game_min_world_size, distance_same: step.game_distance_same, distance_other: step.game_distance_other };
    const cell = (key, max) => number((r.steps[step.folder] || {})[key], { min: 0, max, step: 1, placeholder: game[key] ? String(game[key]) : (key === 'count' ? 'as fit' : '0'), label: `${step.label} ${key}` },
      (v) => { if (v == null) { if (r.steps[step.folder]) { delete r.steps[step.folder][key]; if (!Object.keys(r.steps[step.folder]).length) delete r.steps[step.folder]; } } else rule()[key] = v; });
    return el('div', { class: 'e-step' }, el('span', { class: 'k' }, step.label, el('small', { class: 'mono muted', text: step.folder.replace(/monument\//g, '') })),
      STEP_FIELDS.map(([key, label, max]) => el('label', { class: 'e-step-in' }, el('span', { class: 'lbl', text: label }), cell(key, max))));
  });
  out.push(el('section', { class: 'panel e-sec', 'data-section': 'Placement steps', 'data-id': 'steps' },
    el('div', { class: 'panel-head' }, el('h2', { text: 'Placement steps' }), el('span', { class: 'hint', text: 'the game places monuments group by group; an empty box keeps the game\'s value (in grey)' })),
    el('div', { class: 'e-steps' }, el('div', { class: 'e-step head' }, el('span', { text: 'Step' }), STEP_FIELDS.map(([, label]) => el('span', { class: 'r', text: label }))), stepRows)));

  out.push(section('block', 'Block', 'prefabs whose path contains one of these lines are never placed',
    lines(r.block_extra, (value) => { r.block_extra = value; }, 'More path fragments to block', 'one per line, e.g. autospawn/decor/roadside/'),
    el('p', { class: 'e-note', text: 'Rocks, cliffs and decor can be blocked here too. Blocking bushes or trees fills the game\'s log with warnings.' })));

  out.push(section('file', 'The settings as a file', 'what Make maps sends; Save as keeps it under a name of its own',
    el('pre', { id: 'e-preview-text', class: 'mono file' }, JSON.stringify(r, null, 1))));

  if (!SECTION_IDS.includes(E.section)) E.section = 'basics';
  for (const sec of out) sec.hidden = sec.dataset.id !== E.section;
  const at = SECTION_IDS.indexOf(E.section);
  const label = (id) => NAV.flatMap(([, list]) => list).find(([key]) => key === id)[1];
  const pager = el('div', { class: 'e-pager' },
    at > 0 ? el('button', { class: 'btn', type: 'button', onclick: () => openSection(SECTION_IDS[at - 1]) }, '← ' + label(SECTION_IDS[at - 1])) : el('span'),
    at < SECTION_IDS.length - 1 ? el('button', { class: 'btn', type: 'button', onclick: () => openSection(SECTION_IDS[at + 1]) }, label(SECTION_IDS[at + 1]) + ' →') : null);
  const wide = ['monuments', 'steps'].includes(E.section);
  $('e-sections').className = 'e-sections' + (wide ? ' wide' : '');
  $('e-sections').replaceChildren(...out, pager);
  $('e-body').classList.toggle('wide', wide);
  $('editor').classList.toggle('no-help', !E.help);
  renderChanges();
  renderSummary();
}

// What these settings will make, in a few words, beside the section being edited.
function renderSummary() {
  const box = $('e-summary');
  if (!box || !E.recipe) return;
  const r = E.recipe;
  const t = r.terrain;
  const tally = monumentTally();
  const ring = RING;
  const middle = r.centre.monument ? (E.labels.get(r.centre.monument) || r.centre.monument) : 'nothing';
  const changed = changes().length;
  refill(box,
    el('div', { class: 'panel-head' }, el('h2', { text: 'These settings' }), changed ? el('span', { class: 'tag warn', text: `${changed} changed` }) : null),
    el('div', { class: 'panel-body' },
      el('div', { class: 'sum-counts' }, ['want', 'any', 'block'].map((state) => el('span', { class: 's-' + state }, el('i'), el('b', { text: String(tally[state]) }), ' ' + STATE_LABEL[state].toLowerCase()))),
      cells([['Size', fmt(r.size)], ['Land', t.land_share != null ? `${Math.round(t.land_share * 100)} % of the square` : 'as the game'],
        ['Hills', t.profile ? 'lower' : (t.height_scale != null && t.height_scale !== 1 ? `× ${t.height_scale}` : 'as the game')],
        ['Shallows', t.shallows ? 'wide' : 'as the game'], ['Rail ring', ring[r.rail_ring]], ['Road ring', ring[r.road_ring]],
        ['In the middle', middle], ['Building on roads', r.build_on_roads ? 'allowed' : 'not allowed'],
        ['Custom prefabs', r.templates.length ? String(r.templates.length) : 'none']]),
      el('button', { class: 'btn primary wide-btn', type: 'button', onclick: generate, text: 'Make maps' })));
}

function monumentTally() {
  const tally = { want: 0, any: 0, block: 0 };
  for (const m of E.data.catalog.monuments) tally[E.recipe.monuments[m.prefab] || 'any'] += 1;
  return tally;
}

// The section list: in the sidebar beside the settings, and as buttons above them on a phone.
function renderNav() {
  if (!E.recipe || !$('e-nav')) return;
  const tally = monumentTally();
  const changedIn = {};
  for (const change of changes()) for (const id of change.sections) changedIn[id] = (changedIn[id] || 0) + 1;
  const extra = (id) => {
    if (id === 'monuments') return el('span', { class: 'nav-counts' }, ['want', 'any', 'block'].map((state) => el('span', { class: 's-' + state, title: STATE_LABEL[state], text: String(tally[state]) })));
    if (id === 'prefabs' && E.recipe.templates.length) return el('span', { class: 'badge', text: String(E.recipe.templates.length) });
    return null;
  };
  const mark = (id) => (changedIn[id] ? el('span', { class: 'nav-chg', title: `${changedIn[id]} changed here`, text: String(changedIn[id]) }) : null);
  const optionText = (id, text) => (id === 'monuments' ? `${text} · ${tally.want} prefer, ${tally.block} never` : text) + (changedIn[id] ? ` · ${changedIn[id]} changed` : '');
  // Built once, then changed in place: a list redrawn under the pointer loses the click that is landing on it.
  if (!$('e-nav').querySelector('.nav-item')) {
    const help = el('label', { class: 'check nav-help' }, (() => {
      const box = el('input', { type: 'checkbox' });
      box.checked = E.help;
      box.addEventListener('change', () => { E.help = box.checked; try { localStorage.setItem('rustmapgen-help', E.help ? '1' : '0'); } catch { /* this visit only */ } $('editor').classList.toggle('no-help', !E.help); });
      return box;
    })(), el('span', { text: 'Explain each setting' }));
    fill($('e-nav'), NAV.map(([group, list]) => el('div', { class: 'nav-group' }, el('h3', { text: group }),
      list.map(([id, text]) => el('button', { type: 'button', class: 'nav-item', 'data-id': id, onclick: () => openSection(id) }, el('span', { text }), el('span', { class: 'nav-extra' }))))), help);
    const picker = el('select', { 'aria-label': 'Section' }, NAV.map(([group, list]) => el('optgroup', { label: group }, list.map(([id, text]) => el('option', { value: id, text })))));
    picker.addEventListener('change', () => openSection(picker.value));
    fill($('e-nav-top'), el('label', {}, 'Section', picker));
  }
  for (const item of $('e-nav').querySelectorAll('.nav-item')) {
    const id = item.dataset.id;
    item.classList.toggle('on', id === E.section);
    if (id === E.section) item.setAttribute('aria-current', 'true'); else item.removeAttribute('aria-current');
    const box = item.querySelector('.nav-extra');
    refill(box, mark(id), extra(id));
  }
  const picker = $('e-nav-top').querySelector('select');
  for (const option of picker.options) {
    const text = optionText(option.value, NAV.flatMap(([, list]) => list).find(([key]) => key === option.value)[1]);
    if (option.textContent !== text) option.textContent = text;
  }
  if (picker.value !== E.section) picker.value = E.section;
}

function openSection(id) {
  E.section = id;
  try { localStorage.setItem('rustmapgen-section', id); } catch { /* this visit only */ }
  renderEditor();
  $('editor').scrollTop = 0;
}

function renderBar() {
  const r = E.recipe;
  const select = el('select', { id: 'e-recipe', 'aria-label': 'Recipe', 'data-testid': 'recipe-select' },
    E.from ? el('option', { value: '', text: 'Map: ' + baseTitle().replace(/^the settings of /, ''), selected: true }) : null,
    !E.from && !E.data.recipes.some((x) => x.name === E.base && !x.broken) ? el('option', { value: '', text: `${E.base || 'custom'} (no longer saved)`, selected: true }) : null,
    el('optgroup', { label: 'Presets' }, E.data.recipes.filter((x) => !x.saved && !x.broken).sort(byStart).map((x) => el('option', { value: x.name, text: `${x.title} (${x.size})`, selected: !E.from && x.name === E.base }))),
    E.data.recipes.some((x) => x.saved) ? el('optgroup', { label: 'Saved' }, E.data.recipes.filter((x) => x.saved && !x.broken).map((x) => el('option', { value: x.name, text: `${x.title || x.name} (${x.size})`, selected: !E.from && x.name === E.base }))) : null);
  select.addEventListener('change', () => {
    if (!select.value) return;
    const next = E.data.recipes.find((x) => x.name === select.value);
    if (!leaveChanges(next && next.title ? next.title : select.value)) { select.value = E.from ? '' : E.base; return; }
    openRecipe(select.value);
  });
  const size = number(r.size, { min: 1000, max: 6000, step: 50, label: 'Size' }, (v) => {
    E.recipe.size = v ?? 4250;
    const moved = fitLimits(E.recipe);
    renderEditor();
    if (moved.length) say(fitted(moved).trim());
  });
  size.id = 'e-size';
  // Saved settings and the presets can be removed, but the game default: there is always that to start from.
  const listed = !E.from && E.base !== START_PRESET && E.data.recipes.find((x) => x.name === E.base && !x.broken);
  $('e-bar').replaceChildren(
    el('label', {}, 'Settings', select),
    el('label', {}, 'Size', size),
    el('label', {}, 'Seed', el('input', { id: 'e-seed', type: 'number', min: 1, max: 2147483647, placeholder: 'random', class: 'num-in wide-in' })),
    el('label', {}, 'Maps', el('input', { id: 'e-count', type: 'number', min: 1, max: 12, value: 1, class: 'num-in' })),
    el('label', { class: 'grow' }, 'Note', el('input', { id: 'e-note', type: 'text', maxlength: 200, placeholder: 'what this run is trying' })),
    el('span', { id: 'e-game-box', class: 'e-game' }),
    el('span', { class: 'e-actions' },
      el('button', { class: 'btn primary', 'data-testid': 'generate', onclick: generate, text: 'Make maps' }),
      el('button', { id: 'e-save', class: 'btn', onclick: saveAs, text: 'Save as…' }),
      listed ? el('button', { class: 'btn', 'data-testid': 'remove-recipe', onclick: removeSaved, text: 'Remove' }) : null),
    el('p', { id: 'e-staging', class: 'e-message', 'data-testid': 'staging-notice', hidden: true }),
    el('div', { id: 'e-changes', class: 'e-changes', 'data-testid': 'changes', hidden: true }),
    el('p', { id: 'e-message', class: 'e-message', hidden: true }));
  for (const id of ['e-seed', 'e-count', 'e-note']) $(id).addEventListener('input', saveDraft);
  renderGame();
}

// The game the maps are made on: Public, or the Next update once a RustMapGen update has declared it, until the forced
// wipe's update is out ("staging" in the code). Shown again whenever the panel's status says it opened or closed.
function renderGame() {
  const box = $('e-game-box');
  const note = $('e-staging');
  if (!box || !note) return;
  const w = (S.status && S.status.staging) || {};
  if (!w.open) E.game = 'public';
  const shown = box.dataset.open === String(!!w.open);
  if (!shown) {
    box.dataset.open = String(!!w.open);
    box.replaceChildren(w.open ? el('label', {}, 'Game', el('select', { id: 'e-game', 'data-testid': 'game',
      onchange: (event) => { E.game = event.target.value; renderGame(); } },
      el('option', { value: 'public', text: 'Public', selected: E.game !== 'staging' }),
      el('option', { value: 'staging', text: w.wipe ? `Next update (forced wipe ${w.wipe})` : 'Next update (test)', selected: E.game === 'staging' }))) : '');
  }
  const g = (S.install && S.install.staging) || {};
  note.hidden = E.game !== 'staging';
  if (note.hidden) return;
  note.className = 'e-message' + (g.mod_broken ? ' bad' : ' warn');
  fill(note, g.mod_broken ? `The mod does not work on build ${g.mod_broken.build} of the next update yet. `
    : `${w.notice || 'The next update is open for testing outside its days.'}${g.installed ? '' : ' Its game (about 6 GB) is downloaded before the first map.'} `,
  !g.mod_broken && (w.changes || []).length ? el('button', { type: 'button', class: 'link', text: 'What changes', onclick: () => showChanges(true) }) : null,
  g.mod_broken ? el('button', { type: 'button', class: 'link', text: 'Check for updates', onclick: () => { U.open = true; checkApp(); } }) : null);
}

function say(text, bad, action, about) {
  const box = $('e-message');
  fill(box, text, action ? el('button', { type: 'button', class: 'link', text: action.text, onclick: action.onclick }) : null);
  box.className = 'e-message' + (bad ? ' bad' : '');
  box.dataset.about = about || '';
  box.hidden = false;
}

// The word that maps were queued holds while one is being made or waits, and goes once none is.
function queueSettled(status) {
  const box = $('e-message');
  if (box && box.dataset.about === 'queue' && !status.running && !(status.queue && status.queue.length)) box.hidden = true;
}

function openRecipe(name) {
  const found = E.data.recipes.find((x) => x.name === name && !x.broken) || E.data.recipes.find((x) => !x.broken);
  E.recipe = copy(found);
  delete E.recipe.saved;
  E.baseRecipe = copy(E.recipe);
  // Saved before the limits followed the size: brought within them, and the change shows as a change.
  const moved = fitLimits(E.recipe);
  Object.assign(E, { base: found.name, from: null, showAll: false });
  renderBar();
  renderEditor();
  saveDraft();
  if (moved.length) say(fitted(moved).trim());
}

// The settings a map was made with, as a starting point.
function openSettings(settings, mapName) {
  E.recipe = Object.assign(copy(E.data.defaults), copy(settings));
  const moved = fitLimits(E.recipe);
  E.baseRecipe = copy(E.recipe);
  Object.assign(E, { base: settings.name || 'custom', from: mapName, showAll: false });
  renderBar();
  renderEditor();
  saveDraft();
  if (moved.length) say(fitted(moved).trim());
}

async function generate() {
  // A preset or saved settings with only another size are sent by name; anything else as the settings themselves.
  const known = E.data.recipes.some((x) => x.name === E.base && !x.broken);
  const edited = E.from || !known || changes().some((change) => change.key !== 'size');
  const name = edited ? `${(E.base || 'custom').replace(/-edit$/, '')}-edit`.slice(0, 48) : E.base;
  const body = { seed: $('e-seed').value, count: $('e-count').value, note: $('e-note').value, size: E.recipe.size, game: E.game || 'public' };
  if (edited) body.settings = Object.assign({}, E.recipe, { name }); else body.recipe = E.base;
  try {
    const answer = await api('/api/generate', { method: 'POST', headers: SEND, body: JSON.stringify(body) });
    say(`${answer.message}. A map takes a few minutes: watch it under Maps.`, false, { text: 'Watch it', onclick: () => { showMaps(); showLive(); } }, 'queue');
    renderStatus(answer.generator);
  } catch (error) { say(error.message, true); }
}

// The file name of saved settings, made from the name given to them: small letters, digits and dashes.
function fileName(title) {
  const name = title.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40).replace(/-+$/, '');
  return name.length >= 2 ? name : `mine-${Date.now().toString(36)}`;
}

async function saveAs() {
  const saved = E.data.recipes.find((x) => x.name === E.base && x.saved);
  const title = (prompt('A name for these settings:', saved ? saved.title || saved.name : `${E.recipe.title || 'Settings'} (mine)`) || '').trim().slice(0, 120);
  if (!title) return;
  // The name given is the one shown; the file takes it in plain letters, and never a preset's.
  let name = fileName(title);
  if (E.data.presets.includes(name)) name += '-mine';
  const there = E.data.recipes.find((x) => x.name === name && x.saved);
  if (there && name !== E.base && !confirm(`There are saved settings called "${there.title || there.name}". Replace them?`)) return;
  try {
    // A preset's words about itself are not true of it once changed.
    const source = changes().length ? '' : E.recipe.source;
    await api('/api/recipes/' + encodeURIComponent(name), { method: 'PUT', headers: SEND, body: JSON.stringify(Object.assign({}, E.recipe, { name, title, source })) });
    await loadRecipes();
    openRecipe(name);
    say(`Saved as "${title}".`);
  } catch (error) { say(error.message, true); }
}

// Saved settings of your own, changed: written back under their name.
async function saveHere() {
  const name = E.base;
  try {
    await api('/api/recipes/' + encodeURIComponent(name), { method: 'PUT', headers: SEND, body: JSON.stringify(Object.assign({}, E.recipe, { name })) });
    await loadRecipes();
    openRecipe(name);
    say(`Saved to "${baseTitle()}".`);
  } catch (error) { say(error.message, true); }
}

async function removeSaved() {
  const preset = E.data.presets.includes(E.base);
  if (!confirm(preset ? `Take the preset "${baseTitle()}" off the list? Maps made with it stay.`
    : `Remove the saved settings "${E.base}"? Maps made with them stay.`)) return;
  try {
    await api('/api/recipes/' + encodeURIComponent(E.base), { method: 'DELETE', headers: SEND });
    await loadRecipes();
    openRecipe(E.data.recipes.filter((x) => !x.broken).sort(byStart)[0].name);
  } catch (error) { say(error.message, true); }
}
