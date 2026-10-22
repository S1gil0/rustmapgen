// The 3D view of a map: the map whole, with its sky, sea and trees. Everything is drawn in the browser
// from the files bake3d.py and models3d.py write, which the panel serves under the page's `map3d-data`
// meta tag: `<data>/<id>/3d_*` and `<data>/3d-models/<version>/` (`none` while no models are made).
// `?id=<map id>` picks the map, `?for=<words>` names it, `?quality=low|high` the detail, `?stats=1`
// shows the figures.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { Environment } from './viewer/sky.js';
import { MapGround } from './ground.js';
import { Forest } from './forest.js';
import { AGAIN_AFTER, Monuments, fetchAgain } from './monuments.js';
import { NoBuild } from './nobuild.js';
import { Scenery } from './scenery.js';
import { words } from './words.js';

const params = new URLSearchParams(location.search);
const coarse = matchMedia('(pointer: coarse)').matches || Math.min(innerWidth, innerHeight) < 600;
const still = matchMedia('(prefers-reduced-motion: reduce)').matches;
// pixels: how many screen pixels one cell of the ground may cover before a finer level is used.
// rocks: the near models of the scenery within so many metres; angle: the far ones while that wide (radians).
const QUALITY = {
  low: { ratio: 1, pixels: 8, trees: 150, budget: 150000, rocks: 120, angle: 0.014, shadows: false },
  auto: coarse ? { ratio: 1.5, pixels: 6, trees: 250, budget: 300000, rocks: 200, angle: 0.009, shadows: true }
    : { ratio: 2, pixels: 4, trees: 450, budget: 1200000, rocks: 350, angle: 0.005, shadows: true },
  high: { ratio: 2, pixels: 2.5, trees: 700, budget: 3000000, rocks: 600, angle: 0.003, shadows: true },
};
const qualityName = QUALITY[params.get('quality')] ? params.get('quality') : 'auto';
const quality = QUALITY[qualityName];
const $ = (id) => document.getElementById(id);

async function pixels(url) {
  const response = await fetchAgain(url);
  if (!response.ok) throw new Error(`${url}: ${response.status}`);
  const bitmap = await createImageBitmap(await response.blob(), { colorSpaceConversion: 'none', premultiplyAlpha: 'none' });
  const canvas = document.createElement('canvas');
  canvas.width = bitmap.width;
  canvas.height = bitmap.height;
  const context = canvas.getContext('2d', { willReadFrequently: true });
  context.drawImage(bitmap, 0, 0);
  return context.getImageData(0, 0, bitmap.width, bitmap.height).data;
}

// Tried once more after a while when it fails, like fetchAgain.
function texture(url) {
  const load = () => new Promise((resolve, reject) => new THREE.TextureLoader().load(url, resolve, undefined, reject));
  return load().catch(() => new Promise((resolve) => setTimeout(resolve, AGAIN_AFTER)).then(load));
}

// heights.png and rivers.png: 16 bits a sample, red high, green low.
function heightsFrom(rgba, n) {
  const out = new Float32Array(n * n);
  for (let i = 0; i < n * n; i++) out[i] = -500 + ((rgba[i * 4] << 8 | rgba[i * 4 + 1]) - 32768) / 32766 * 1000;
  return out;
}
function riversFrom(rgba, n) {
  const out = new Float32Array(n * n);
  for (let i = 0; i < n * n; i++) {
    const value = rgba[i * 4] << 8 | rgba[i * 4 + 1];
    out[i] = value === 0 ? NaN : (value - 32768) / 100;
  }
  return out;
}

const DATA = document.querySelector('meta[name="map3d-data"]').content;
const ICONS = (document.querySelector('meta[name="map3d-icons"]') || {}).content || '/icons/';

class Unavailable extends Error {}

function wordsInPage() {
  for (const element of document.querySelectorAll('[data-word]')) element.textContent = words[element.dataset.word];
  for (const option of $('quality').options) option.textContent = words[option.value];
  document.title = words.title;
}

async function start() {
  wordsInPage();
  const id = params.get('id') || '';
  const label = (params.get('for') || '').slice(0, 80);
  if (!/^[0-9a-f]{16}$/.test(id)) throw new Unavailable();
  $('title').textContent = label ? `${words.title} · ${label}` : words.title;
  if (label) document.title = `${words.title} · ${label}`;
  $('quality').value = qualityName;
  $('quality').addEventListener('change', () => { params.set('quality', $('quality').value); location.search = params; });

  const base = `${DATA}/${id}/3d_`;
  const t0 = performance.now();
  const answer = await fetchAgain(base + 'meta.json');
  if (answer.status === 404 || answer.status === 403) throw new Unavailable();
  if (!answer.ok) throw new Error(String(answer.status));
  const meta = await answer.json();
  $('no-models').hidden = meta.models !== 'none';
  const models = `${DATA}/3d-models/${meta.models}/`;
  const json = (url) => fetchAgain(url).then((r) => (r.ok ? r.json() : {})).catch(() => ({}));
  const [heightPixels, riverPixels, colour, mix, trees, sceneryBytes, monumentIndex, sceneryIndex] = await Promise.all([
    pixels(base + 'heights.png'), pixels(base + 'rivers.png'), texture(base + 'ground.webp'), texture(base + 'mix.webp'),
    fetchAgain(base + 'trees.bin').then((r) => r.arrayBuffer()), fetchAgain(base + 'scenery.bin').then((r) => (r.ok ? r.arrayBuffer() : new ArrayBuffer(0))),
    json(models + 'monuments/index.json'), json(models + 'scenery/index.json'),
  ]);
  const loaded = performance.now();
  const n = meta.samples;
  const map = { size: meta.size, n, heights: heightsFrom(heightPixels, n), rivers: riversFrom(riverPixels, n), lowest: meta.height_low };

  const canvas = $('view');
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: 'high-performance' });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, quality.ratio));
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.shadowMap.enabled = quality.shadows;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.shadowMap.autoUpdate = false;
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(50, 1, 1, 40000);
  const env = new Environment(renderer, scene, { hours: 14, shadowSize: coarse ? 1024 : 2048, coverage: 0.5, fogDensity: 0.0001 });
  scene.environmentIntensity = 1.1;
  const ground = new MapGround(renderer, env.uniforms, map, colour, mix);
  map.rivers = null;
  scene.add(ground.group);
  const forest = new Forest(env.uniforms, trees, meta.tree_kinds, meta.size, (x, z) => ground.heightAt(x, z));
  scene.add(forest.group);
  const monuments = new Monuments(env.uniforms);
  const scenery = new Scenery(env.uniforms, ground);
  await Promise.all([monuments.load(meta.placed || [], models + 'monuments/', monumentIndex),
    scenery.load(meta.scenery_kinds || [], sceneryBytes, models + 'scenery/', sceneryIndex)]);
  scene.add(monuments.group, scenery.group);
  const built = performance.now();
  let dirty = true;
  let envStale = true;
  // A near model that arrives is drawn at once, and casts its shadow.
  monuments.onChange = scenery.onChange = () => { dirty = true; renderer.shadowMap.needsUpdate = true; };

  // Rails and roads: lines laid on the ground every 6 m.
  const lineGroups = {};
  for (const [kind, colourHex, lift] of [['Rail', '#2a211b', 0.7], ['Road', '#d8cfbd', 0.35]]) {
    const points = [];
    for (const flat of meta.lines[kind] || []) {
      let last = null;
      for (let k = 0; k + 1 < flat.length; k += 2) {
        const x = flat[k], z = flat[k + 1];
        if (last) {
          const steps = Math.max(1, Math.ceil(Math.hypot(x - last[0], z - last[1]) / 6));
          for (let s = 0; s < steps; s++) {
            const ax = last[0] + (x - last[0]) * s / steps, az = last[1] + (z - last[1]) * s / steps;
            const bx = last[0] + (x - last[0]) * (s + 1) / steps, bz = last[1] + (z - last[1]) * (s + 1) / steps;
            points.push(ax, ground.heightAt(ax, az) + lift, -az, bx, ground.heightAt(bx, bz) + lift, -bz);
          }
        }
        last = [x, z];
      }
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(points, 3));
    const lines = new THREE.LineSegments(geometry, new THREE.LineBasicMaterial({ color: colourHex }));
    lines.frustumCulled = false;
    scene.add(lines);
    lineGroups[kind] = lines;
  }

  // Names of the monuments, placed over the scene every frame.
  const labelLayer = $('labels');
  const labels = meta.monuments.map((m) => {
    const element = document.createElement('div');
    element.className = 'label' + (m.minor ? ' minor' : '');
    if (m.icon) {
      const img = document.createElement('img');
      img.src = `${ICONS}${m.icon}.svg`;
      img.alt = '';
      element.append(img);
    } else {
      const dot = document.createElement('i');
      dot.className = 'dot';
      element.append(dot);
    }
    const name = document.createElement('span');
    name.textContent = m.label;
    element.append(name);
    labelLayer.append(element);
    const y = Math.max(m.y, ground.heightAt(m.x, m.z), 0) + 14;
    return { m, element, world: new THREE.Vector3(m.x, y, -m.z), width: (m.icon ? 26 : 12) + m.label.length * (m.minor ? 6 : 6.8), shown: true };
  }).sort((a, b) => a.m.rank - b.m.rank);

  const controls = new OrbitControls(camera, canvas);
  controls.enableDamping = true;
  controls.dampingFactor = 0.09;
  controls.rotateSpeed = 0.7;
  controls.zoomSpeed = 1.1;
  controls.screenSpacePanning = false;
  controls.zoomToCursor = true;
  controls.maxPolarAngle = Math.PI / 2 - 0.02;
  controls.minDistance = 6;
  controls.maxDistance = meta.size * 1.9;
  controls.addEventListener('change', () => { dirty = true; });

  function overview() {
    // Far enough for the square's height, and for its width on a tall screen.
    const reach = Math.max(meta.size * 1.1, meta.size * 0.55 / (Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)) * camera.aspect));
    controls.maxDistance = Math.max(meta.size * 1.9, reach * 1.2);
    controls.target.set(0, Math.max(0, ground.heightAt(0, 0)), 0);
    camera.position.set(reach * 0.16, reach * 0.75, reach * 0.65);
    controls.update();
    dirty = true;
  }
  // `?cam=x,z,distance,turn,tilt`: look at game position (x, z) from `distance` metres, turned
  // `turn` degrees from the south and `tilt` degrees down from straight overhead.
  function look(x, z, distance, turn, tilt) {
    const a = THREE.MathUtils.degToRad(turn), b = THREE.MathUtils.degToRad(tilt);
    controls.target.set(x, Math.max(0, ground.heightAt(x, z)), -z);
    camera.position.set(x + Math.sin(a) * Math.sin(b) * distance, controls.target.y + Math.cos(b) * distance, -z + Math.cos(a) * Math.sin(b) * distance);
    controls.update();
    dirty = true;
  }

  const show = { names: true, trees: true, clouds: true };
  const toggle = (box, apply) => { $(box).addEventListener('change', (event) => { apply(event.target.checked); dirty = true; }); };
  toggle('show-names', (on) => { show.names = on; labelLayer.style.display = on ? '' : 'none'; });
  toggle('show-trees', (on) => { show.trees = on; if (!on) forest.hide(); forest.group.visible = on; renderer.shadowMap.needsUpdate = true; });
  toggle('show-rails', (on) => { lineGroups.Rail.visible = on; });
  toggle('show-roads', (on) => { lineGroups.Road.visible = on; });
  lineGroups.Road.visible = false;
  toggle('show-rocks', (on) => { scenery.group.visible = on; renderer.shadowMap.needsUpdate = true; });
  toggle('show-monuments', (on) => { monuments.group.visible = on; renderer.shadowMap.needsUpdate = true; });
  toggle('show-clouds', (on) => { show.clouds = on; env.clouds.visible = on; });
  // Where the game does not let a base be built: off until the box is ticked (or `?nobuild=1`); the first
  // time, the volumes and their shells, then the ground's red in slices. A models version without them
  // switches the box off.
  const noBuild = new NoBuild(ground);
  scene.add(noBuild.group);
  let noBuildAsked = null;
  const showNoBuild = (on) => {
    if (on && !noBuildAsked) {
      noBuildAsked = noBuild.load(models, meta.placed || [], meta.scenery_kinds || [], scenery)
        .then(() => {
          noBuild.shells();
          noBuild.visible = $('show-nobuild').checked;
          dirty = true;
          return noBuild.mask(meta.size);
        })
        .then(() => { times.nobuild_ms = Math.round(performance.now() - t0); dirty = true; })
        .catch((error) => { console.error(error); $('show-nobuild').checked = false; $('show-nobuild').disabled = true; noBuild.visible = false; });
    }
    noBuild.visible = on;
  };
  toggle('show-nobuild', showNoBuild);
  if (params.get('nobuild') === '1') $('show-nobuild').checked = true;
  if ($('show-nobuild').checked) showNoBuild(true);
  const hours = $('hours');
  const clock = (h) => `${String(Math.floor(h)).padStart(2, '0')}:${String(Math.round((h % 1) * 60)).padStart(2, '0')}`;
  hours.addEventListener('input', () => { env.setTime(Number(hours.value)); $('hours-value').textContent = clock(Number(hours.value)); envStale = true; dirty = true; });
  $('overview').addEventListener('click', overview);
  $('hide').addEventListener('click', () => document.body.classList.add('folded'));
  $('unfold').addEventListener('click', () => document.body.classList.remove('folded'));
  if (coarse) document.body.classList.add('folded');

  // The free camera: the first press of W A S D, E or Q
  // takes the pointer and the mouse looks around; W and S fly the way the camera looks, A and D
  // sideways, E and Q up and down, Shift three times faster. A click, Esc or the wheel give the
  // usual view back. It stays a player's height over the ground or the sea.
  const MOVE_KEYS = { KeyW: [0, 0, 1], KeyS: [0, 0, -1], KeyA: [-1, 0, 0], KeyD: [1, 0, 0], KeyE: [0, 1, 0], KeyQ: [0, -1, 0] };
  const LOOK = 0.0022;        // radians a pixel of mouse movement turns
  const EYE = 1.7;            // metres over the ground
  const FOV = 50;
  const FREE_FOV = 62;
  const held = new Set();
  const pace = new THREE.Vector3();
  let fast = false;
  let free = null;            // while flying: { yaw, pitch, distance, locked, asked, skip }

  function enterFree() {
    if (free) return;
    const ahead = new THREE.Vector3().subVectors(controls.target, camera.position);
    const distance = ahead.length() || 1;
    ahead.divideScalar(distance);
    free = { yaw: Math.atan2(-ahead.x, -ahead.z), pitch: Math.asin(Math.max(-1, Math.min(1, ahead.y))), distance, locked: false, asked: 0, skip: 0 };
    // What is left of a drag would be played when the usual view comes back.
    if (controls._sphericalDelta) controls._sphericalDelta.set(0, 0, 0);
    if (controls._panOffset) controls._panOffset.set(0, 0, 0);
    controls.enabled = false;
    document.body.classList.add('flying');
    dirty = true;
  }

  // Back to the usual view, turning around the point of the ground the camera looks at.
  function leaveFree() {
    const was = free;
    if (!was) return;
    free = null;
    held.clear();
    pace.set(0, 0, 0);
    if (document.pointerLockElement === canvas) document.exitPointerLock();
    const pitch = Math.max(0.05 - Math.PI / 2, Math.min(-0.05, was.pitch));
    const cos = Math.cos(pitch);
    const way = new THREE.Vector3(-Math.sin(was.yaw) * cos, Math.sin(pitch), -Math.cos(was.yaw) * cos);
    const eye = camera.position;
    let distance = 300;
    for (let d = 4; d < meta.size * 2; d *= 1.08) {
      const x = eye.x + way.x * d, y = eye.y + way.y * d, z = eye.z + way.z * d;
      if (Math.max(0, ground.heightAt(x, -z)) >= y) { distance = d; break; }
    }
    controls.target.copy(eye).addScaledVector(way, Math.min(controls.maxDistance, Math.max(controls.minDistance, distance)));
    controls.enabled = true;
    controls.update();
    document.body.classList.remove('flying');
    dirty = true;
  }

  // Asks the browser to let the canvas hold the pointer; until it agrees the camera flies without looking.
  function lock() {
    if (!free || free.locked || !canvas.requestPointerLock) return;
    const now = performance.now();
    if (now - free.asked < 400) return;
    free.asked = now;
    try {
      const asked = canvas.requestPointerLock();
      if (asked && asked.catch) asked.catch(() => {});
    } catch (error) { /* the next key asks again */ }
  }

  function aim() {
    camera.rotation.set(free.pitch, free.yaw, 0, 'YXZ');
    const cos = Math.cos(free.pitch);
    controls.target.set(-Math.sin(free.yaw) * cos, Math.sin(free.pitch), -Math.cos(free.yaw) * cos).multiplyScalar(free.distance).add(camera.position);
  }

  document.addEventListener('pointerlockchange', () => {
    if (!free) return;
    if (document.pointerLockElement === canvas) { free.locked = true; free.skip = 1; } else if (free.locked) leaveFree();
  });
  document.addEventListener('mousemove', (event) => {
    if (!free || !free.locked) return;
    // The first movement after the pointer is taken can be one big jump.
    if (free.skip > 0) { free.skip--; return; }
    const most = 180;
    free.yaw -= Math.max(-most, Math.min(most, event.movementX || 0)) * LOOK;
    free.pitch = Math.max(-1.5, Math.min(1.5, free.pitch - Math.max(-most, Math.min(most, event.movementY || 0)) * LOOK));
    aim();
    dirty = true;
  });
  document.addEventListener('pointerdown', (event) => {
    if (!free || event.target !== canvas) return;
    const holding = free.locked;
    leaveFree();
    // A click that gave the pointer back does nothing else; without it, it starts a drag.
    if (holding) { event.stopPropagation(); event.preventDefault(); }
  }, true);
  document.addEventListener('wheel', () => { if (free && !free.locked) leaveFree(); }, true);
  document.addEventListener('keydown', (event) => {
    if (event.metaKey || event.ctrlKey || event.altKey) { held.clear(); return; }
    if (event.target instanceof Element && event.target.matches('input:not([type=checkbox]), select, textarea')) return;
    fast = event.shiftKey;
    if (!MOVE_KEYS[event.code]) return;
    if (held.has(event.code)) { lock(); return; }
    if (event.repeat) return;
    held.add(event.code);
    enterFree();
    lock();
    dirty = true;
  });
  document.addEventListener('keyup', (event) => { fast = event.shiftKey; held.delete(event.code); });
  addEventListener('blur', () => { held.clear(); fast = false; });

  // The free camera's flight for the keys held, easing in and out: at a run near the ground and
  // faster the higher it is. True while there is movement left to draw.
  function walk(seconds) {
    if (!free) { pace.set(0, 0, 0); return false; }
    let right = 0, up = 0, ahead = 0;
    for (const code of held) { right += MOVE_KEYS[code][0]; up += MOVE_KEYS[code][1]; ahead += MOVE_KEYS[code][2]; }
    const all = Math.hypot(right, up, ahead) || 1;
    const ease = still ? 1 : Math.min(1, seconds * 11);
    pace.x += (right / all - pace.x) * ease;
    pace.y += (up / all - pace.y) * ease;
    pace.z += (ahead / all - pace.z) * ease;
    if (!right && !up && !ahead && pace.lengthSq() < 0.0009) { pace.set(0, 0, 0); return false; }
    const eye = camera.position;
    const below = Math.max(0, ground.heightAt(eye.x, -eye.z));
    const step = Math.min(500, 8 + (eye.y - below) * 0.7) * (fast ? 3 : 1) * seconds;
    const cos = Math.cos(free.pitch), sinYaw = Math.sin(free.yaw), cosYaw = Math.cos(free.yaw);
    const reach = meta.size * 0.62;
    const x = Math.min(reach, Math.max(-reach, eye.x + (-sinYaw * cos * pace.z + cosYaw * pace.x) * step));
    const z = Math.min(reach, Math.max(-reach, eye.z + (-cosYaw * cos * pace.z - sinYaw * pace.x) * step));
    const floor = Math.max(0, ground.heightAt(x, -z)) + EYE;
    eye.set(x, Math.min(Math.max(eye.y + (Math.sin(free.pitch) * pace.z + pace.y) * step, floor), meta.size * 1.2), z);
    aim();
    return true;
  }

  // The angle of view eases wider while flying.
  function widen(seconds) {
    const goal = free ? FREE_FOV : FOV;
    const gap = goal - camera.fov;
    if (!gap) return false;
    camera.fov = Math.abs(gap) < 0.05 || still ? goal : camera.fov + gap * Math.min(1, seconds * 5);
    return true;
  }

  function resize() {
    const width = innerWidth, height = innerHeight;
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    dirty = true;
  }
  addEventListener('resize', resize);
  resize();
  const cam = (params.get('cam') || '').split(',').map(Number);
  if (cam.length === 5 && cam.every(Number.isFinite)) look(...cam); else overview();

  let shadowFocus = null;
  let last = performance.now();
  let lastAnimated = 0;
  let renders = 0;
  let rendersAt = performance.now();
  let fps = 0;
  let groundInfo = { tiles: [], triangles: 0 };
  const projected = new THREE.Vector3();
  const gpu = (() => {
    const gl = renderer.getContext();
    const info = gl.getExtension('WEBGL_debug_renderer_info');
    return info ? gl.getParameter(info.UNMASKED_RENDERER_WEBGL) : 'unknown';
  })();
  // For the browser checks: where the camera is and whether it flies.
  $('stats').hidden = params.get('stats') !== '1';
  window.__map3d = { stats: {}, noBuild, view: () => ({ x: camera.position.x, y: camera.position.y, z: camera.position.z, free: Boolean(free), locked: Boolean(free && free.locked), yaw: free ? free.yaw : null, fov: camera.fov }) };

  // The focus stays on the ground and inside the map's reach; the camera stays over the ground.
  function keepOnGround() {
    const target = controls.target;
    const reach = meta.size * 0.62;
    const cx = Math.min(reach, Math.max(-reach, target.x)), cz = Math.min(reach, Math.max(-reach, target.z));
    if (cx !== target.x || cz !== target.z) { camera.position.x += cx - target.x; camera.position.z += cz - target.z; target.x = cx; target.z = cz; }
    const floor = Math.max(0, ground.heightAt(target.x, -target.z));
    const dy = (floor - target.y) * 0.3;
    if (Math.abs(dy) > 0.01) { target.y += dy; camera.position.y += dy; }
    const above = Math.max(0, ground.heightAt(camera.position.x, -camera.position.z)) + 2.5;
    if (camera.position.y < above) camera.position.y = above;
  }

  function hidden(from, to) {
    for (let k = 1; k < 24; k++) {
      const f = k / 24;
      const x = from.x + (to.x - from.x) * f, y = from.y + (to.y - from.y) * f, z = from.z + (to.z - from.z) * f;
      if (ground.heightAt(x, -z) > y + 1.5) return true;
    }
    return false;
  }

  function placeLabels() {
    if (!show.names) return;
    const width = innerWidth, height = innerHeight;
    const placed = [];
    const distance = camera.position.distanceTo(controls.target);
    for (const label of labels) {
      projected.copy(label.world).project(camera);
      const far = camera.position.distanceTo(label.world);
      let visible = projected.z < 1 && Math.abs(projected.x) < 1.1 && Math.abs(projected.y) < 1.1 && !(label.m.minor && far > 1800)
        && !hidden(camera.position, label.world);
      const x = (projected.x + 1) / 2 * width, y = (1 - projected.y) / 2 * height;
      const iconOnly = distance > meta.size * 0.9 && label.m.minor;
      const w = iconOnly ? 24 : label.width;
      if (visible) {
        for (const box of placed) if (x < box[2] && x + w > box[0] && y - 11 < box[3] && y + 11 > box[1]) { visible = false; break; }
      }
      if (visible) {
        placed.push([x - 4, y - 11, x + w + 4, y + 11]);
        label.element.style.transform = `translate(${(x - 11).toFixed(1)}px, ${(y - 11).toFixed(1)}px)`;
        label.element.classList.toggle('only-icon', iconOnly);
      }
      if (visible !== label.shown) { label.element.style.display = visible ? '' : 'none'; label.shown = visible; }
    }
  }

  // While flying, the trees and shadows follow a point of the ground ahead of the camera.
  const ahead = new THREE.Vector3();
  function draw(now) {
    const above = camera.position.y - Math.max(0, ground.heightAt(camera.position.x, -camera.position.z));
    const distance = free ? Math.max(40, above * 2) : camera.position.distanceTo(controls.target);
    camera.near = Math.min(100, Math.max(0.15, above * 0.15));
    camera.far = distance + meta.size * 3;
    camera.updateProjectionMatrix();
    camera.updateMatrixWorld();
    scene.fog.density = 0.6 / (distance * 1.3 + 1600);
    groundInfo = ground.update(camera, renderer.domElement.height, quality.pixels * renderer.getPixelRatio());
    // Trees and their shadows around the focus, only when it is near enough to see them.
    if (scenery.group.visible && scenery.update(camera.position, quality.rocks, quality.angle, above)) renderer.shadowMap.needsUpdate = true;
    if (monuments.group.visible) monuments.update(camera.position);
    let focus = controls.target;
    if (free) {
      const reach = Math.min(quality.trees * 0.4, 60 + above);
      ahead.set(camera.position.x - Math.sin(free.yaw) * reach, 0, camera.position.z - Math.cos(free.yaw) * reach);
      ahead.y = Math.max(0, ground.heightAt(ahead.x, -ahead.z));
      focus = ahead;
    }
    if (show.trees && distance < 2600) {
      if (forest.update(focus.x, -focus.z, quality.trees, quality.budget)) renderer.shadowMap.needsUpdate = true;
    } else if (forest.hide()) renderer.shadowMap.needsUpdate = true;
    const radius = Math.min(450, Math.max(60, distance * 0.9));
    if (quality.shadows && (!shadowFocus || shadowFocus.distanceTo(focus) > radius / 8 || Math.abs(shadowFocus.radius - radius) > radius / 5)) {
      shadowFocus = focus.clone();
      shadowFocus.radius = radius;
      env.fit(focus, radius);
    }
    ground.tick(now / 1000);
    if (show.clouds && !still) env.tick(now / 1000);
    renderer.render(scene, camera);
    if (!times.first_frame_ms) times.first_frame_ms = Math.round(performance.now() - t0);
    placeLabels();
    renders++;
  }

  $('loading').style.display = 'none';
  const ready = performance.now();
  const times = { download_ms: Math.round(loaded - t0), build_ms: Math.round(built - loaded), total_ms: Math.round(ready - t0) };

  function frame(now) {
    requestAnimationFrame(frame);
    const seconds = Math.min(0.1, (now - last) / 1000);
    last = now;
    if (walk(seconds)) dirty = true;
    if (widen(seconds)) dirty = true;
    if (!free) {
      if (controls.update()) dirty = true;
      keepOnGround();
    }
    // Water and clouds move four times a second while nothing else does; on a phone they wait for the camera.
    if (!still && !coarse && now - lastAnimated > 250) { lastAnimated = now; dirty = true; }
    if (dirty) {
      dirty = false;
      draw(now);
    } else if (envStale) {
      envStale = false;
      env.refresh();
      renderer.shadowMap.needsUpdate = true;
      dirty = true;
    }
    if (now - rendersAt > 1000) {
      fps = Math.round(renders * 1000 / (now - rendersAt));
      renders = 0;
      rendersAt = now;
      const info = renderer.info.render;
      const stats = { fps, triangles: info.triangles, calls: info.calls, ground_triangles: groundInfo.triangles, tiles: groundInfo.tiles,
        trees: forest.shown, tree_triangles: forest.triangles, monument_triangles: monuments.group.visible ? monuments.triangles() : 0,
        monuments: monuments.group.children.length, monument_kinds: monuments.kinds.size, monuments_missing: monuments.missing,
        scenery: scenery.count, scenery_near: scenery.shown[0], scenery_far: scenery.shown[1], scenery_triangles: scenery.triangles, scenery_missing: scenery.missing,
        nobuild: noBuild.volumes.length, nobuild_triangles: noBuild.triangles || 0, gpu, quality: qualityName, ...times };
      window.__map3d.stats = stats;
      $('stats').innerHTML = `<b>${fps}</b> frames/s · <b>${(info.triangles / 1e6).toFixed(2)} M</b> triangles · ${info.calls} draws\n`
        + `ground ${(groundInfo.triangles / 1e6).toFixed(2)} M · trees ${forest.shown.toLocaleString('en')} (${(forest.triangles / 1e6).toFixed(2)} M)`
        + ` · monuments ${monuments.group.children.length} (${(stats.monument_triangles / 1e6).toFixed(2)} M)${monuments.missing.length ? `, ${monuments.missing.length} kinds without a model` : ''}\n`
        + `rocks and pylons ${scenery.count.toLocaleString('en')}: ${scenery.shown[0].toLocaleString('en')} near, ${scenery.shown[1].toLocaleString('en')} far (${(scenery.triangles / 1e6).toFixed(2)} M)\n`
        + `loaded in ${(times.download_ms / 1000).toFixed(1)} s + built in ${(times.build_ms / 1000).toFixed(1)} s · ${qualityName}\n${gpu}`;
    }
  }
  requestAnimationFrame(frame);
}

start().catch((error) => {
  if (!(error instanceof Unavailable)) console.error(error);
  $('loading').textContent = error instanceof Unavailable ? words.unavailable : words.failed;
});
