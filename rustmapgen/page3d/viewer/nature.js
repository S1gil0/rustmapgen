// Trees, bushes, ore nodes and dead wood around a base, when the asset pack has no model for a
// kind: low-poly shapes built here, one geometry per kind with its colours baked per vertex,
// drawn as one instanced mesh per kind. Sizes are a full-grown plant at scale 1, in metres.
import * as THREE from 'three';
import { hash2, rng } from './noise.js';
import { skyFog } from './sky.js';

const UP = new THREE.Vector3(0, 1, 0);

// Collects pieces (three.js primitives, moved into place and coloured) into one geometry.
class Sculpt {
  constructor(seed) {
    this.random = rng(seed);
    this.position = [];
    this.normal = [];
    this.colour = [];
    this.m = new THREE.Matrix4();
    this.q = new THREE.Quaternion();
    this.v = new THREE.Vector3();
    this.c = new THREE.Color();
    this.low = new THREE.Color();
    this.high = new THREE.Color();
  }

  // `shade(colour, y01, normalY, face)` may adjust the colour of each vertex.
  add(geometry, { at = [0, 0, 0], turn = [0, 0, 0], size = [1, 1, 1], low, high, jitter = 0, facets = true, vary = 0.1, shade }) {
    const g = geometry;
    const points = g.getAttribute('position');
    if (jitter) {
      // Vertices that share a place must move together, or the shape cracks open.
      const moved = new Map();
      for (let i = 0; i < points.count; i++) {
        const key = `${points.getX(i).toFixed(3)},${points.getY(i).toFixed(3)},${points.getZ(i).toFixed(3)}`;
        if (!moved.has(key)) moved.set(key, 1 + (this.random() - 0.5) * 2 * jitter);
        const k = moved.get(key);
        points.setXYZ(i, points.getX(i) * k, points.getY(i) * k, points.getZ(i) * k);
      }
    }
    this.m.compose(this.v.set(...at), this.q.setFromEuler(new THREE.Euler(...turn)), new THREE.Vector3(...size));
    g.applyMatrix4(this.m);
    if (jitter && !facets) g.computeVertexNormals();
    const flat = g.index ? g.toNonIndexed() : g;
    if (facets) flat.computeVertexNormals();
    const p = flat.getAttribute('position');
    const n = flat.getAttribute('normal');
    flat.computeBoundingBox();
    const y0 = flat.boundingBox.min.y;
    const span = Math.max(flat.boundingBox.max.y - y0, 0.001);
    this.low.set(low);
    this.high.set(high || low);
    let tint = 1;
    for (let i = 0; i < p.count; i++) {
      if (i % 3 === 0) tint = 1 + (this.random() - 0.5) * 2 * vary;
      const t = (p.getY(i) - y0) / span;
      this.c.copy(this.low).lerp(this.high, t).multiplyScalar(tint);
      if (shade) shade(this.c, t, n.getY(i), Math.floor(i / 3), this.random);
      this.position.push(p.getX(i), p.getY(i), p.getZ(i));
      this.normal.push(n.getX(i), n.getY(i), n.getZ(i));
      this.colour.push(this.c.r, this.c.g, this.c.b);
    }
    geometry.dispose();
    return this;
  }

  // A leafy mass: a dented ball, darker underneath.
  blob(at, radius, low, high, squash = 0.85, jitter = 0.14) {
    return this.add(new THREE.IcosahedronGeometry(radius, 1), {
      at, size: [1, squash, 1], turn: [0, this.random() * 6, 0], low, high, jitter, vary: 0.12,
      shade: (c, t, ny) => { if (ny < -0.2) c.multiplyScalar(0.62); },
    });
  }

  // A straight limb from `from` to `to`.
  limb(from, to, r0, r1, low, high, sides = 6, options = {}) {
    const a = new THREE.Vector3(...from);
    const b = new THREE.Vector3(...to);
    const length = a.distanceTo(b);
    const g = new THREE.CylinderGeometry(r1, r0, length, sides, options.rings || 1);
    const q = new THREE.Quaternion().setFromUnitVectors(UP, b.clone().sub(a).normalize());
    const e = new THREE.Euler().setFromQuaternion(q);
    const mid = a.clone().add(b).multiplyScalar(0.5);
    return this.add(g, { at: [mid.x, mid.y, mid.z], turn: [e.x, e.y, e.z], low, high, facets: options.facets ?? false, vary: options.vary ?? 0.06, shade: options.shade });
  }

  geometry() {
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(this.position, 3));
    g.setAttribute('normal', new THREE.Float32BufferAttribute(this.normal, 3));
    g.setAttribute('color', new THREE.Float32BufferAttribute(this.colour, 3));
    g.computeBoundingSphere();
    return g;
  }
}

const BARK = ['#4f3e2e', '#6a5540'];

function rock(seed, stone, ore, share) {
  const s = new Sculpt(seed);
  const mineral = new THREE.Color();
  const ores = [].concat(ore);
  s.add(new THREE.IcosahedronGeometry(0.78, 1), {
    at: [0, 0.42, 0], size: [1.2, 0.82, 1], low: stone[0], high: stone[1], jitter: 0.24, vary: 0.1,
    shade: (c, t, ny, face, random) => {
      // Whole facets of mineral, so a node reads from far away.
      const pick = hash2(face, seed, 5);
      if (pick < share) c.copy(mineral.set(ores[Math.floor(pick / share * ores.length)])).multiplyScalar(0.9 + random() * 0.2);
      if (ny < -0.3) c.multiplyScalar(0.6);
    },
  });
  s.add(new THREE.IcosahedronGeometry(0.42, 0), { at: [0.72, 0.2, 0.3], low: stone[0], high: stone[1], jitter: 0.2, vary: 0.12 });
  s.add(new THREE.IcosahedronGeometry(0.3, 0), { at: [-0.62, 0.14, -0.46], low: stone[0], high: stone[1], jitter: 0.2, vary: 0.12 });
  return s.geometry();
}

const KINDS = {
  pine() {
    const s = new Sculpt(1);
    s.limb([0, 0, 0], [0, 15.5, 0], 0.34, 0.07, BARK[0], BARK[1], 7);
    for (let i = 0; i < 7; i++) {
      const radius = 3.4 - i * 0.44;
      s.add(new THREE.ConeGeometry(radius, 3.5 - i * 0.12, 9, 1), {
        at: [(s.random() - 0.5) * 0.3, 5 + i * 1.86, (s.random() - 0.5) * 0.3], turn: [0, s.random() * 6, 0],
        low: '#22381f', high: '#4a6c37', jitter: 0.1, vary: 0.1,
        shade: (c, t, ny) => { if (ny < -0.5) c.multiplyScalar(0.5); },
      });
    }
    return s.geometry();
  },
  oak() {
    const s = new Sculpt(2);
    s.limb([0, 0, 0], [0.2, 4.6, 0], 0.5, 0.3, BARK[0], BARK[1], 7);
    s.limb([0.1, 3.6, 0], [2.1, 6.2, 0.6], 0.24, 0.12, BARK[0], BARK[1], 5);
    s.limb([0.1, 3.4, 0], [-1.9, 6.2, -1], 0.24, 0.12, BARK[0], BARK[1], 5);
    s.limb([0.2, 4.2, 0], [0.3, 6.2, 2], 0.2, 0.1, BARK[0], BARK[1], 5);
    const leaf = ['#3f5d27', '#73923f'];
    s.blob([0.2, 7.6, 0], 3.0, ...leaf).blob([2.3, 6.6, 0.7], 2.2, ...leaf).blob([-2.1, 6.7, -1.1], 2.3, ...leaf);
    s.blob([0.4, 6.4, 2.3], 2.0, ...leaf).blob([-0.4, 6.6, -2.4], 1.9, ...leaf).blob([0.5, 9.1, -0.3], 2.0, ...leaf);
    return s.geometry();
  },
  birch() {
    const s = new Sculpt(3);
    s.limb([0, 0, 0], [0.25, 9.4, 0.1], 0.17, 0.06, '#d9d5c8', '#e6e2d6', 6, {
      rings: 9, facets: true, vary: 0.04,
      shade: (c, t, ny, face, random) => { if (random() < 0.16) c.setRGB(0.05, 0.045, 0.04); },
    });
    const leaf = ['#6f8c3a', '#b3c15e'];
    s.blob([0.2, 8.4, 0.1], 1.6, ...leaf, 1.15).blob([1, 7.1, 0.4], 1.25, ...leaf, 1.1).blob([-0.8, 7.4, -0.5], 1.25, ...leaf, 1.1);
    s.blob([0.2, 6.2, -1], 1.0, ...leaf, 1.1).blob([-0.2, 9.9, 0.2], 1.1, ...leaf, 1.2).blob([-0.3, 6.5, 1], 0.95, ...leaf, 1.1);
    return s.geometry();
  },
  palm() {
    const s = new Sculpt(4);
    const spine = (t) => [1.7 * t * t, 8.6 * t, 0.3 * t];
    for (let i = 0; i < 8; i++) {
      s.limb(spine(i / 8), spine((i + 1) / 8), 0.27 - i * 0.012, 0.25 - i * 0.012, i % 2 ? '#7d6c52' : '#93826a', i % 2 ? '#8a7a60' : '#9f8f76', 7, { facets: true, vary: 0.05 });
    }
    const top = new THREE.Vector3(...spine(1));
    const frond = new Sculpt(41);
    for (let f = 0; f < 11; f++) {
      const azimuth = f / 11 * Math.PI * 2 + (frond.random() - 0.5) * 0.3;
      const reach = 3.5 + frond.random() * 0.9;
      const lift = 0.35 + frond.random() * 0.3;
      const out = new THREE.Vector3(Math.cos(azimuth), 0, Math.sin(azimuth));
      const side = new THREE.Vector3(-out.z, 0, out.x);
      const segments = 5;
      const rib = [];
      for (let k = 0; k <= segments; k++) {
        const t = k / segments;
        const droop = lift * t * reach * 0.9 - t * t * reach * 0.62;
        rib.push({ centre: top.clone().addScaledVector(out, t * reach * 0.92).add(new THREE.Vector3(0, droop, 0)), width: 0.12 + Math.sin(Math.min(1, t * 1.25) * Math.PI) * 0.62 });
      }
      for (let k = 0; k < segments; k++) {
        for (const hand of [-1, 1]) {
          const a = rib[k], b = rib[k + 1];
          const a1 = a.centre.clone().addScaledVector(side, hand * a.width).add(new THREE.Vector3(0, -a.width * 0.42, 0));
          const b1 = b.centre.clone().addScaledVector(side, hand * b.width).add(new THREE.Vector3(0, -b.width * 0.42, 0));
          const quad = hand > 0 ? [a.centre, a1, b1, a.centre, b1, b.centre] : [a.centre, b1, a1, a.centre, b.centre, b1];
          const normal = new THREE.Vector3().crossVectors(quad[1].clone().sub(quad[0]), quad[2].clone().sub(quad[0])).normalize();
          const tone = 0.86 + frond.random() * 0.26;
          quad.forEach((p, i) => {
            s.position.push(p.x, p.y, p.z);
            s.normal.push(normal.x, normal.y, normal.z);
            const t = (k + (i === 2 || i === 4 || i === 5 ? 1 : 0)) / segments;
            s.c.set('#335f27').lerp(s.high.set('#7ba046'), t).multiplyScalar(tone);
            s.colour.push(s.c.r, s.c.g, s.c.b);
          });
        }
      }
    }
    for (let i = 0; i < 3; i++) s.add(new THREE.IcosahedronGeometry(0.19, 0), { at: [top.x + Math.cos(i * 2.1) * 0.3, top.y - 0.3, top.z + Math.sin(i * 2.1) * 0.3], low: '#5a4028', vary: 0.1 });
    return s.geometry();
  },
  swamp() {
    const s = new Sculpt(5);
    s.limb([0, 0, 0], [0.5, 3.4, 0.2], 0.62, 0.36, '#3d3428', '#55493a', 7);
    s.limb([0.5, 3.4, 0.2], [0.1, 6.4, -0.3], 0.36, 0.18, '#3d3428', '#55493a', 6);
    s.limb([0.4, 3.0, 0.2], [2.6, 5.2, 0.9], 0.2, 0.06, '#3d3428', '#55493a', 5);
    s.limb([0.4, 3.6, 0.1], [-2.2, 5.6, -0.8], 0.2, 0.06, '#3d3428', '#55493a', 5);
    s.limb([0.2, 5.4, -0.2], [1.2, 7.6, -1.8], 0.14, 0.05, '#3d3428', '#55493a', 5);
    const leaf = ['#3c4a2c', '#66703f'];
    s.blob([0.1, 6.9, -0.3], 2.2, ...leaf, 0.5).blob([2.7, 5.5, 1], 1.6, ...leaf, 0.45).blob([-2.3, 5.9, -0.9], 1.7, ...leaf, 0.45).blob([1.3, 7.8, -1.9], 1.3, ...leaf, 0.5);
    return s.geometry();
  },
  jungle() {
    const s = new Sculpt(6);
    s.limb([0, 0, 0], [0.3, 13.2, 0.1], 0.52, 0.3, '#5a4a38', '#77664e', 8);
    for (let i = 0; i < 4; i++) {
      const a = i / 4 * Math.PI * 2 + 0.4;
      s.limb([Math.cos(a) * 1.25, 0, Math.sin(a) * 1.25], [Math.cos(a) * 0.2, 2.6, Math.sin(a) * 0.2], 0.2, 0.3, '#5a4a38', '#6e5d46', 4, { facets: true });
    }
    s.limb([0.3, 12.2, 0.1], [3.4, 14, 1], 0.2, 0.1, '#5a4a38', '#77664e', 5);
    s.limb([0.3, 12.2, 0.1], [-3.2, 14, -1.4], 0.2, 0.1, '#5a4a38', '#77664e', 5);
    s.limb([0.2, 8.6, 0], [2.4, 10, -1.6], 0.14, 0.07, '#5a4a38', '#77664e', 5);
    const leaf = ['#22501f', '#4f9038'];
    s.blob([0.3, 14.6, 0.1], 3.6, ...leaf, 0.42);
    for (let i = 0; i < 6; i++) {
      const a = i / 6 * Math.PI * 2 + 0.2;
      s.blob([0.3 + Math.cos(a) * 3.7, 14 + (i % 2) * 0.5, 0.1 + Math.sin(a) * 3.7], 2.7, ...leaf, 0.36);
    }
    s.blob([2.6, 10.3, -1.8], 1.5, ...leaf, 0.5);
    return s.geometry();
  },
  cactus() {
    const s = new Sculpt(7);
    const rib = (c, t, ny, face) => { if (face % 2) c.multiplyScalar(1.16); };
    const skin = ['#4f6f40', '#6f9257'];
    s.limb([0, 0, 0], [0, 3.6, 0], 0.32, 0.3, ...skin, 8, { facets: true, shade: rib });
    s.add(new THREE.SphereGeometry(0.3, 8, 3, 0, Math.PI * 2, 0, Math.PI / 2), { at: [0, 3.6, 0], low: skin[1], vary: 0.06 });
    s.limb([0.25, 1.6, 0], [0.95, 1.75, 0], 0.2, 0.2, ...skin, 7, { facets: true, shade: rib });
    s.limb([0.95, 1.65, 0], [0.95, 3.0, 0], 0.2, 0.18, ...skin, 7, { facets: true, shade: rib });
    s.add(new THREE.SphereGeometry(0.18, 7, 3, 0, Math.PI * 2, 0, Math.PI / 2), { at: [0.95, 3.0, 0], low: skin[1], vary: 0.06 });
    s.limb([-0.25, 2.1, 0.05], [-0.85, 2.2, 0.1], 0.18, 0.18, ...skin, 7, { facets: true, shade: rib });
    s.limb([-0.85, 2.12, 0.1], [-0.85, 3.1, 0.1], 0.18, 0.16, ...skin, 7, { facets: true, shade: rib });
    s.add(new THREE.SphereGeometry(0.16, 7, 3, 0, Math.PI * 2, 0, Math.PI / 2), { at: [-0.85, 3.1, 0.1], low: skin[1], vary: 0.06 });
    return s.geometry();
  },
  bush() {
    const s = new Sculpt(8);
    const leaf = ['#3f5a2a', '#76934a'];
    s.blob([0, 0.62, 0], 0.85, ...leaf, 0.8, 0.2).blob([0.66, 0.45, 0.24], 0.6, ...leaf, 0.8, 0.2);
    s.blob([-0.58, 0.48, -0.3], 0.62, ...leaf, 0.8, 0.2).blob([0.1, 0.42, 0.7], 0.52, ...leaf, 0.8, 0.2);
    return s.geometry();
  },
  'ore.stone': () => rock(9, ['#7f7b73', '#a09c92'], ['#d6d1c4', '#c4bfb1'], 0.34),
  'ore.metal': () => rock(10, ['#6c655c', '#8c857a'], ['#a8683c', '#8b9096', '#b97c48'], 0.36),
  'ore.sulfur': () => rock(11, ['#7f7b70', '#9e998c'], ['#d9c536', '#c9b02c'], 0.38),
  'ore.hqm': () => rock(12, ['#5d6066', '#7d8188'], ['#a9cde0', '#d5e6ee'], 0.3),
  log() {
    const s = new Sculpt(13);
    s.limb([-1.7, 0.3, 0], [1.7, 0.34, 0.1], 0.32, 0.27, BARK[0], BARK[1], 8, {
      facets: true, shade: (c, t, ny, face) => { if (face >= 16) c.set('#b89a6a'); },
    });
    s.limb([0.4, 0.5, 0], [0.9, 0.95, 0.4], 0.09, 0.05, BARK[0], BARK[1], 5);
    return s.geometry();
  },
  driftwood() {
    const s = new Sculpt(14);
    s.limb([-1.3, 0.16, 0], [0.5, 0.26, 0.2], 0.17, 0.12, '#a89b85', '#c7bba4', 6, { facets: true });
    s.limb([0.5, 0.26, 0.2], [1.5, 0.62, -0.3], 0.12, 0.05, '#a89b85', '#c7bba4', 5, { facets: true });
    s.limb([-0.2, 0.2, 0.05], [0.3, 0.6, 0.8], 0.07, 0.03, '#a89b85', '#c7bba4', 5, { facets: true });
    return s.geometry();
  },
};

// [width, height] factors that bring the shapes above to the size of the game's models
// (birch 20 m, palm 26 m, ore nodes 4 m across); the others are close as drawn.
const FULL_GROWN = { birch: [1.4, 1.9], palm: [1.7, 2.3], 'ore.stone': [1.7, 1.7], 'ore.metal': [1.7, 1.7], 'ore.sulfur': [1.7, 1.7], 'ore.hqm': [1.7, 1.7] };

// A kind this file does not know: guessed from its name, so a new kind in the scene still shows.
function builderFor(kind) {
  if (KINDS[kind]) return KINDS[kind];
  if (kind.startsWith('ore')) return KINDS['ore.stone'];
  if (/bush|shrub|berry|hemp/.test(kind)) return KINDS.bush;
  if (/log|wood|stump/.test(kind)) return KINDS.log;
  return KINDS.oak;
}

export class Nature {
  constructor(fog) {
    this.group = new THREE.Group();
    this.geometries = new Map();
    this.material = new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.9, metalness: 0, side: THREE.DoubleSide });
    this.material.onBeforeCompile = (shader) => skyFog(shader, fog);
    this.material.customProgramCacheKey = () => 'nature';
  }

  geometry(kind) {
    if (!this.geometries.has(kind)) {
      const geometry = builderFor(kind)();
      // A scene's `scale` is against the game's full-grown model: stretch ours to that size.
      const fit = FULL_GROWN[kind];
      if (fit) geometry.scale(fit[0], fit[1], fit[0]);
      this.geometries.set(kind, geometry);
    }
    return this.geometries.get(kind);
  }

  clear() {
    for (const child of [...this.group.children]) {
      this.group.remove(child);
      child.dispose();
    }
  }

  // `nature` is the scene's block; `models(kind)` gives the asset pack's parts for a kind or null.
  build(nature, models = () => null) {
    this.clear();
    if (!nature || !nature.items || !nature.items.length) return;
    const byKind = new Map();
    for (const item of nature.items) {
      const kind = nature.kinds[item[0]];
      if (kind === undefined) continue;
      if (!byKind.has(kind)) byKind.set(kind, []);
      byKind.get(kind).push(item);
    }
    const matrix = new THREE.Matrix4();
    const position = new THREE.Vector3();
    const turn = new THREE.Quaternion();
    const size = new THREE.Vector3();
    const tint = new THREE.Color();
    for (const [kind, items] of byKind) {
      const real = models(kind);
      const parts = real || [{ geometry: this.geometry(kind), material: this.material }];
      for (const part of parts) {
        const mesh = new THREE.InstancedMesh(part.geometry, part.material, items.length);
        const vary = real ? 0.12 : 0.32;          // a real texture needs less help to look varied
        items.forEach(([, x, y, z, yaw, scale], i) => {
          // Game to viewer space: z is mirrored, so a turn around y runs the other way.
          position.set(x, y, -z);
          turn.setFromAxisAngle(UP, -(yaw || 0) * Math.PI / 180);
          size.setScalar(scale || 1);
          mesh.setMatrixAt(i, matrix.compose(position, turn, size));
          const shade = 1 - vary / 2 + hash2(Math.round(x * 10), Math.round(z * 10), 3) * vary;
          const warm = hash2(Math.round(x * 10), Math.round(z * 10), 8) - 0.5;
          mesh.setColorAt(i, tint.setRGB(shade * (1 + warm * 0.14), shade, shade * (1 - warm * 0.2)));
        });
        mesh.castShadow = true;
        // Leaf cards shading each other turn a crown to mud: they take the light as it comes.
        mesh.receiveShadow = !(real && part.material.userData.leafy);
        mesh.computeBoundingSphere();
        this.group.add(mesh);
      }
    }
  }

  dispose() {
    this.clear();
    for (const geometry of this.geometries.values()) geometry.dispose();
    this.material.dispose();
  }
}
