// The monuments: one model a kind, the collision shapes of the game's own files (models3d.py), coloured
// by surface and thinned. Every monument shows its far model first; a kind's near model is
// fetched once the camera comes near one of them, and from then on drawn up close. Placed where the
// map file stands each monument.
import * as THREE from 'three';
import { skyFog } from './viewer/sky.js';

// The switch from the near model to the far one, in metres: a share of the model's size, at least.
const FAR_FROM = 260;

// One model level of models3d.py: gzipped, a JSON header, then positions, colours and triangles.
export function parse(buffer) {
  const view = new DataView(buffer);
  const length = view.getUint32(0, true);
  const header = JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, 4, length)));
  const [x0, y0, z0, x1, y1, z1] = header.box;
  const span = [Math.max(x1 - x0, 1e-3), Math.max(y1 - y0, 1e-3), Math.max(z1 - z0, 1e-3)];
  let offset = 4 + length;
  const levels = header.levels.map((level) => {
    const raw = new Int16Array(buffer, offset, level.vertices * 3);
    offset += Math.ceil(level.vertices * 6 / 4) * 4;
    const rgb = new Uint8Array(buffer, offset, level.vertices * 3);
    offset += Math.ceil(level.vertices * 3 / 4) * 4;
    const wide = level.index || 4;
    const index = wide === 2 ? new Uint16Array(buffer, offset, level.triangles * 3) : new Uint32Array(buffer, offset, level.triangles * 3);
    offset += Math.ceil(level.triangles * 3 * wide / 4) * 4;
    const position = new Float32Array(level.vertices * 3);
    const colour = new Float32Array(level.vertices * 3);
    const c = new THREE.Color();
    for (let i = 0; i < level.vertices; i++) {
      position[i * 3] = x0 + (raw[i * 3] + 32768) / 65535 * span[0];
      position[i * 3 + 1] = y0 + (raw[i * 3 + 1] + 32768) / 65535 * span[1];
      position[i * 3 + 2] = z0 + (raw[i * 3 + 2] + 32768) / 65535 * span[2];
      c.setRGB(rgb[i * 3] / 255, rgb[i * 3 + 1] / 255, rgb[i * 3 + 2] / 255, THREE.SRGBColorSpace);
      colour[i * 3] = c.r; colour[i * 3 + 1] = c.g; colour[i * 3 + 2] = c.b;
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(position, 3));
    geometry.setAttribute('color', new THREE.BufferAttribute(colour, 3));
    geometry.setIndex(new THREE.BufferAttribute(index, 1));
    geometry.computeBoundingSphere();
    return { name: level.name, geometry, triangles: level.triangles };
  });
  return { box: header.box, levels };
}

// The map host refuses an address that asks too much at once (429 for ten seconds, without the CORS
// header, so the browser sees a network error): a fetch refused so is tried once more after that.
export const AGAIN_AFTER = 11000;
export async function fetchAgain(url) {
  try {
    const response = await fetch(url);
    if (response.status !== 429) return response;
  } catch (error) { /* refused at the edge, or the network: once more */ }
  await new Promise((resolve) => setTimeout(resolve, AGAIN_AFTER));
  return fetch(url);
}

// A model file, gunzipped in the browser (the files are stored compressed).
export async function fetchModel(url) {
  const response = await fetchAgain(url);
  if (!response.ok) throw new Error(`${url}: ${response.status}`);
  const body = url.endsWith('.gz') ? response.body.pipeThrough(new DecompressionStream('gzip')) : response.body;
  return parse(await new Response(body).arrayBuffer());
}

// All the far models of a folder in one file (models3d.py `bundle`), so a page asks once and not once a
// kind. A function from a key to its far model (null when the file has none), or null without the file.
export async function fetchFar(base) {
  try {
    const response = await fetchAgain(base + 'far.bin.gz');
    if (!response.ok) return null;
    const buffer = await new Response(response.body.pipeThrough(new DecompressionStream('gzip'))).arrayBuffer();
    const length = new DataView(buffer).getUint32(0, true);
    const table = JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, 4, length)));
    const start = 4 + length;
    return (key) => (table[key] ? parse(buffer.slice(start + table[key][0], start + table[key][0] + table[key][1])) : null);
  } catch (error) {
    return null;
  }
}

export class Monuments {
  constructor(fog) {
    this.group = new THREE.Group();
    this.material = new THREE.MeshStandardMaterial({ vertexColors: true, flatShading: true, roughness: 0.86, metalness: 0.04, side: THREE.DoubleSide });
    this.material.onBeforeCompile = (shader) => skyFog(shader, fog);
    this.material.customProgramCacheKey = () => 'map-monument';
    this.missing = [];
    this.kinds = new Map();         // short name -> { lods, near: null | 'asked' | true, switch, files }
    this.onChange = () => {};
  }

  // `placed`: 3d_meta.json's list; `base`: the folder of the monument models; `index`: its index.json.
  async load(placed, base, index) {
    this.base = base;
    const shorts = [...new Set(placed.map((item) => item.short))];
    const fars = new Map();
    const bundled = await fetchFar(base);
    await Promise.all(shorts.map(async (short) => {
      const row = index[short];
      const far = row && row.levels.find((level) => level.name === 'far');
      if (!far) { this.missing.push(short); return; }
      try {
        fars.set(short, (bundled && bundled(short)) || await fetchModel(base + far.file));
      } catch (error) {
        this.missing.push(short);
      }
    }));
    for (const item of placed) {
      const model = fars.get(item.short);
      if (!model) continue;
      const [x0, y0, z0, x1, y1, z1] = model.box;
      const size = Math.hypot(x1 - x0, y1 - y0, z1 - z0);
      if (!this.kinds.has(item.short)) {
        this.kinds.set(item.short, { lods: [], near: null, switch: Math.max(FAR_FROM, size * 1.6), file: index[item.short].levels.find((level) => level.name === 'near')?.file });
      }
      const lod = new THREE.LOD();
      const mesh = new THREE.Mesh(model.levels[0].geometry, this.material);
      mesh.receiveShadow = true;
      mesh.userData.triangles = model.levels[0].triangles;
      lod.addLevel(mesh, 0);
      lod.position.set(...item.p);
      lod.quaternion.set(...item.q);
      lod.scale.set(...item.s);
      lod.userData.short = item.short;
      this.kinds.get(item.short).lods.push(lod);
      this.group.add(lod);
    }
    this.group.updateMatrixWorld(true);
  }

  // Fetches the near model of every kind with a monument within reach of the camera.
  update(eye) {
    for (const [short, kind] of this.kinds) {
      if (kind.near || !kind.file) continue;
      if (!kind.lods.some((lod) => lod.position.distanceTo(eye) < kind.switch * 1.5)) continue;
      kind.near = 'asked';
      fetchModel(this.base + kind.file).then((model) => {
        const level = model.levels[0];
        for (const lod of kind.lods) {
          lod.levels[0].distance = kind.switch;
          const mesh = new THREE.Mesh(level.geometry, this.material);
          mesh.castShadow = true;
          mesh.receiveShadow = true;
          mesh.userData.triangles = level.triangles;
          lod.addLevel(mesh, 0);
        }
        kind.near = true;
        this.onChange(short);
      }, () => { kind.near = 'failed'; });
    }
  }

  // Triangles of the levels in use, after a render.
  triangles() {
    let total = 0;
    for (const lod of this.group.children) total += lod.levels[lod.getCurrentLevel()].object.userData.triangles;
    return total;
  }
}
