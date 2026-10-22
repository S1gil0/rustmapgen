// Where the game does not let a base be built: the no-build volumes of the monuments and of the scenery
// (power line pylons, substations, tunnel entrances), the game's own colliders on layer 29 (models3d.py
// `no_build`), placed as the map file stands each prefab. Shown two ways: red on the ground wherever a
// foundation would touch one (a mask the ground shader reads), and their shells, faint with a brighter
// rim, so a monument's bubble shows its size. Roads and rails stop building by the ground's topology,
// not by these volumes; the page says so.
import * as THREE from 'three';
import { fetchAgain } from './monuments.js';

const CELL = 2;                 // metres a texel of the ground mask
const LOW = -0.5, HIGH = 3;     // the slice of a column a foundation takes, metres from the ground
const MESH_CELL = 8;            // metres a cell of a mesh volume's triangle grid

async function fetchJson(url) {
  const response = await fetchAgain(url);
  if (!response.ok) throw new Error(`${url}: ${response.status}`);
  return JSON.parse(await new Response(response.body.pipeThrough(new DecompressionStream('gzip'))).text());
}

// The twelve edges of a box, corner to corner, in its own -1..1 space.
const BOX_EDGES = [[-1, -1, -1, 1, -1, -1], [-1, 1, -1, 1, 1, -1], [-1, -1, 1, 1, -1, 1], [-1, 1, 1, 1, 1, 1],
  [-1, -1, -1, -1, 1, -1], [1, -1, -1, 1, 1, -1], [-1, -1, 1, -1, 1, 1], [1, -1, 1, 1, 1, 1],
  [-1, -1, -1, -1, -1, 1], [1, -1, -1, 1, -1, 1], [-1, 1, -1, -1, 1, 1], [1, 1, -1, 1, 1, 1]];

// `rim` 1: brighter where the surface turns away (a bubble); 0: one faint shade (a box's faces).
const shellMaterial = (rim) => new THREE.ShaderMaterial({
  transparent: true, depthWrite: false, side: THREE.DoubleSide,
  uniforms: { colour: { value: new THREE.Color('#ff3b2a') }, rim: { value: rim } },
  vertexShader: /* glsl */`
    varying vec3 vNormal; varying vec3 vView;
    void main() {
      vec4 place = modelViewMatrix * vec4(position, 1.0);
      vNormal = normalize(normalMatrix * normal);
      vView = -place.xyz;
      gl_Position = projectionMatrix * place;
    }`,
  fragmentShader: /* glsl */`
    uniform vec3 colour; uniform float rim; varying vec3 vNormal; varying vec3 vView;
    void main() {
      float edge = 1.0 - abs(dot(normalize(vNormal), normalize(vView)));
      gl_FragColor = vec4(colour, mix(0.035, 0.04 + 0.42 * pow(edge, 3.0), rim));
    }`,
});

export class NoBuild {
  constructor(ground) {
    this.ground = ground;
    this.group = new THREE.Group();
    this.group.renderOrder = 10;
    this.volumes = [];
    this.ready = false;
  }

  // `models`: the folder of the models version; `placed`: 3d_meta.json's monuments; `keys`: its
  // scenery_kinds; `scenery`: the loaded Scenery (each piece's kind and matrix).
  async load(models, placed, keys, scenery) {
    const [own, sceneryOwn] = await Promise.all([fetchJson(models + 'monuments/nobuild.json.gz'), fetchJson(models + 'scenery/nobuild.json.gz')]);
    const matrix = new THREE.Matrix4();
    const turn = new THREE.Quaternion();
    for (const item of placed) {
      if (!own[item.short]) continue;
      matrix.compose(new THREE.Vector3(...item.p), turn.set(...item.q), new THREE.Vector3(...item.s));
      for (const shape of own[item.short]) this.add(shape, matrix);
    }
    for (let i = 0; i < scenery.count; i++) {
      const shapes = sceneryOwn[keys[scenery.kind[i]]];
      if (!shapes) continue;
      matrix.fromArray(scenery.matrices, i * 16);
      for (const shape of shapes) this.add(shape, matrix);
    }
  }

  // One shape of models3d.py `no_build` (prefab space) placed by `m` (viewer space).
  add(shape, m) {
    const grow = m.getMaxScaleOnAxis();
    const linear = new THREE.Matrix3().setFromMatrix4(m);
    const point = (d, k) => new THREE.Vector3(d[k], d[k + 1], d[k + 2]).applyMatrix4(m);
    const axis = (d, k) => new THREE.Vector3(d[k], d[k + 1], d[k + 2]).applyMatrix3(linear);
    if (shape.s) this.volumes.push({ kind: 's', c: point(shape.s, 0), r: shape.s[3] * grow });
    else if (shape.b) {
      const axes = [axis(shape.b, 3), axis(shape.b, 6), axis(shape.b, 9)];
      this.volumes.push({ kind: 'b', c: point(shape.b, 0), axes, inverse: axes.map((a) => 1 / Math.max(a.lengthSq(), 1e-9)) });
    } else if (shape.c) this.volumes.push({ kind: 'c', a: point(shape.c, 0), b: point(shape.c, 3), r: shape.c[6] * grow });
    else if (shape.m) {
      const p = new Float32Array(shape.m.p.length);
      const v = new THREE.Vector3();
      for (let k = 0; k < p.length; k += 3) v.set(shape.m.p[k], shape.m.p[k + 1], shape.m.p[k + 2]).applyMatrix4(m).toArray(p, k);
      this.volumes.push({ kind: 'm', p, t: Uint32Array.from(shape.m.t) });
    }
  }

  // The ground mask: 255 where a foundation's slice of the column touches a volume. Done in slices of
  // about 12 ms so the page keeps drawing; `size` is the map's.
  async mask(size) {
    const n = Math.ceil(size / CELL) + 1;
    const x0 = -size / 2, z0 = -size / 2;
    const data = new Uint8Array(n * n);
    let started = performance.now();
    for (const volume of this.volumes) {
      const [lx, hx, lz, hz] = footprint(volume);          // game x and z
      const i0 = Math.max(0, Math.floor((lx - x0) / CELL)), i1 = Math.min(n - 1, Math.ceil((hx - x0) / CELL));
      const j0 = Math.max(0, Math.floor((lz - z0) / CELL)), j1 = Math.min(n - 1, Math.ceil((hz - z0) / CELL));
      const test = tester(volume);
      for (let j = j0; j <= j1; j++) {
        const z = z0 + j * CELL;
        for (let i = i0; i <= i1; i++) {
          if (data[j * n + i]) continue;
          const x = x0 + i * CELL;
          const h = this.ground.heightAt(x, z);
          if (test(x, -z, h + LOW, h + HIGH)) data[j * n + i] = 255;
        }
      }
      if (performance.now() - started > 12) {
        await new Promise((resolve) => setTimeout(resolve, 0));
        started = performance.now();
      }
    }
    Object.assign(this, { data, n, x0, z0 });     // kept for checks against the game (tests/map-3d)
    const texture = new THREE.DataTexture(data, n, n, THREE.RedFormat, THREE.UnsignedByteType);
    texture.magFilter = texture.minFilter = THREE.LinearFilter;
    texture.needsUpdate = true;
    this.ground.uniforms.noBuild.value = texture;
    this.ground.uniforms.noBuildFrame.value.set(x0, z0, CELL, n);
  }

  // The shells: round volumes (spheres, capsules, meshes) lit at the rim like a bubble; boxes as their
  // edges over faint faces, since a large box seen at a slant would cover the view in red.
  shells() {
    const round = { positions: [], normals: [], index: [] }, flat = { positions: [], normals: [], index: [] };
    const edges = [];
    const put = (into, geometry, place, turn) => {
      const p = geometry.attributes.position, q = geometry.attributes.normal, base = into.positions.length / 3;
      const v = new THREE.Vector3(), w = new THREE.Vector3();
      for (let k = 0; k < p.count; k++) {
        place(v.fromBufferAttribute(p, k)).toArray(into.positions, into.positions.length);
        turn(w.fromBufferAttribute(q, k)).normalize().toArray(into.normals, into.normals.length);
      }
      const own = geometry.index ? geometry.index.array : [...Array(p.count).keys()];
      for (const k of own) into.index.push(base + k);
      geometry.dispose();
    };
    for (const volume of this.volumes) {
      if (volume.kind === 's') {
        put(round, new THREE.SphereGeometry(1, 32, 16), (v) => v.multiplyScalar(volume.r).add(volume.c), (w) => w);
      } else if (volume.kind === 'b') {
        const [a, b, c] = volume.axes, [ia, ib, ic] = volume.inverse;
        const corner = (x, y, z) => new THREE.Vector3().copy(volume.c).addScaledVector(a, x).addScaledVector(b, y).addScaledVector(c, z);
        put(flat, new THREE.BoxGeometry(2, 2, 2), (v) => corner(v.x, v.y, v.z),
          (w) => new THREE.Vector3().addScaledVector(a, w.x * ia).addScaledVector(b, w.y * ib).addScaledVector(c, w.z * ic));
        for (const [x0, y0, z0, x1, y1, z1] of BOX_EDGES) {
          corner(x0, y0, z0).toArray(edges, edges.length);
          corner(x1, y1, z1).toArray(edges, edges.length);
        }
      } else if (volume.kind === 'c') {
        const along = new THREE.Vector3().subVectors(volume.b, volume.a);
        const middle = new THREE.Vector3().addVectors(volume.a, volume.b).multiplyScalar(0.5);
        const rotate = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), along.clone().normalize());
        put(round, new THREE.CapsuleGeometry(volume.r, along.length(), 6, 16), (v) => v.applyQuaternion(rotate).add(middle), (w) => w.applyQuaternion(rotate));
      } else {
        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute('position', new THREE.BufferAttribute(volume.p, 3));
        geometry.setIndex(new THREE.BufferAttribute(volume.t, 1));
        geometry.computeVertexNormals();
        put(round, geometry, (v) => v, (w) => w);
      }
    }
    this.triangles = 0;
    for (const [into, rim] of [[round, 1], [flat, 0]]) {
      if (!into.index.length) continue;
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.Float32BufferAttribute(into.positions, 3));
      geometry.setAttribute('normal', new THREE.Float32BufferAttribute(into.normals, 3));
      geometry.setIndex(into.index);
      const mesh = new THREE.Mesh(geometry, shellMaterial(rim));
      mesh.renderOrder = 10;
      this.group.add(mesh);
      this.triangles += into.index.length / 3;
    }
    if (edges.length) {
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.Float32BufferAttribute(edges, 3));
      const lines = new THREE.LineSegments(geometry, new THREE.LineBasicMaterial({ color: '#ff3b2a', transparent: true, opacity: 0.65, depthWrite: false }));
      lines.renderOrder = 11;
      this.group.add(lines);
    }
  }

  set visible(on) {
    this.group.visible = on;
    this.ground.uniforms.noBuildOn.value = on ? 1 : 0;
  }
}

// A volume's reach on the ground: [low x, high x, low z, high z] in game x and z (viewer z turned).
function footprint(volume) {
  if (volume.kind === 's') return [volume.c.x - volume.r, volume.c.x + volume.r, -volume.c.z - volume.r, -volume.c.z + volume.r];
  if (volume.kind === 'b') {
    const ex = volume.axes.reduce((s, a) => s + Math.abs(a.x), 0), ez = volume.axes.reduce((s, a) => s + Math.abs(a.z), 0);
    return [volume.c.x - ex, volume.c.x + ex, -volume.c.z - ez, -volume.c.z + ez];
  }
  if (volume.kind === 'c') {
    return [Math.min(volume.a.x, volume.b.x) - volume.r, Math.max(volume.a.x, volume.b.x) + volume.r,
      Math.min(-volume.a.z, -volume.b.z) - volume.r, Math.max(-volume.a.z, -volume.b.z) + volume.r];
  }
  let lx = Infinity, hx = -Infinity, lz = Infinity, hz = -Infinity;
  for (let k = 0; k < volume.p.length; k += 3) {
    lx = Math.min(lx, volume.p[k]); hx = Math.max(hx, volume.p[k]);
    lz = Math.min(lz, -volume.p[k + 2]); hz = Math.max(hz, -volume.p[k + 2]);
  }
  return [lx, hx, lz, hz];
}

// Whether the vertical segment at viewer (x, z) from y0 to y1 touches a volume.
function tester(volume) {
  if (volume.kind === 's') {
    const { c, r } = volume;
    return (x, z, y0, y1) => {
      const dx = x - c.x, dz = z - c.z, dy = Math.min(Math.max(c.y, y0), y1) - c.y;
      return dx * dx + dy * dy + dz * dz <= r * r;
    };
  }
  if (volume.kind === 'b') {
    const { c, axes, inverse } = volume;
    return (x, z, y0, y1) => {
      let from = 0, to = 1;
      for (let k = 0; k < 3; k++) {
        const a = axes[k];
        const t0 = ((x - c.x) * a.x + (y0 - c.y) * a.y + (z - c.z) * a.z) * inverse[k];
        const slope = (y1 - y0) * a.y * inverse[k];
        if (Math.abs(slope) < 1e-9) {
          if (Math.abs(t0) > 1) return false;
          continue;
        }
        const s0 = (-1 - t0) / slope, s1 = (1 - t0) / slope;
        from = Math.max(from, Math.min(s0, s1));
        to = Math.min(to, Math.max(s0, s1));
        if (from > to) return false;
      }
      return true;
    };
  }
  if (volume.kind === 'c') {
    const { a, b, r } = volume;
    return (x, z, y0, y1) => segmentDistance(x, y0, z, y1, a, b) <= r;
  }
  // A mesh: the heights where the column crosses its surface, paired into the stretches inside it.
  const { p, t } = volume;
  const [lx, hx, lz, hz] = footprint(volume);
  const across = Math.max(1, Math.ceil((hx - lx) / MESH_CELL)), down = Math.max(1, Math.ceil((hz - lz) / MESH_CELL));
  const cells = Array.from({ length: across * down }, () => []);
  for (let k = 0; k < t.length; k += 3) {
    const xs = [p[t[k] * 3], p[t[k + 1] * 3], p[t[k + 2] * 3]], zs = [-p[t[k] * 3 + 2], -p[t[k + 1] * 3 + 2], -p[t[k + 2] * 3 + 2]];
    const i0 = Math.max(0, Math.floor((Math.min(...xs) - lx) / MESH_CELL)), i1 = Math.min(across - 1, Math.floor((Math.max(...xs) - lx) / MESH_CELL));
    const j0 = Math.max(0, Math.floor((Math.min(...zs) - lz) / MESH_CELL)), j1 = Math.min(down - 1, Math.floor((Math.max(...zs) - lz) / MESH_CELL));
    for (let j = j0; j <= j1; j++) for (let i = i0; i <= i1; i++) cells[j * across + i].push(k);
  }
  return (x, z, y0, y1) => {
    const gz = -z;
    const i = Math.floor((x - lx) / MESH_CELL), j = Math.floor((gz - lz) / MESH_CELL);
    if (i < 0 || j < 0 || i >= across || j >= down) return false;
    const heights = [];
    for (const k of cells[j * across + i]) {
      const ax = p[t[k] * 3], az = p[t[k] * 3 + 2], bx = p[t[k + 1] * 3], bz = p[t[k + 1] * 3 + 2], cx = p[t[k + 2] * 3], cz = p[t[k + 2] * 3 + 2];
      const d = (bz - cz) * (ax - cx) + (cx - bx) * (az - cz);
      if (Math.abs(d) < 1e-9) continue;
      const u = ((bz - cz) * (x - cx) + (cx - bx) * (z - cz)) / d;
      const v = ((cz - az) * (x - cx) + (ax - cx) * (z - cz)) / d;
      if (u < 0 || v < 0 || u + v > 1) continue;
      heights.push(u * p[t[k] * 3 + 1] + v * p[t[k + 1] * 3 + 1] + (1 - u - v) * p[t[k + 2] * 3 + 1]);
    }
    heights.sort((m, n) => m - n);
    for (let k = 0; k + 1 < heights.length; k += 2) if (heights[k] <= y1 && heights[k + 1] >= y0) return true;
    return false;
  };
}

// The shortest distance between the vertical segment (x, y0..y1, z) and the segment a-b.
function segmentDistance(x, y0, z, y1, a, b) {
  const d1 = new THREE.Vector3(0, y1 - y0, 0), d2 = new THREE.Vector3().subVectors(b, a);
  const r = new THREE.Vector3(x - a.x, y0 - a.y, z - a.z);
  const aa = d1.dot(d1), ee = d2.dot(d2), f = d2.dot(r), c = d1.dot(r), bb = d1.dot(d2);
  const denom = aa * ee - bb * bb;
  let s = denom > 1e-9 ? THREE.MathUtils.clamp((bb * f - c * ee) / denom, 0, 1) : 0;
  let t = (bb * s + f) / Math.max(ee, 1e-9);
  if (t < 0) { t = 0; s = THREE.MathUtils.clamp(-c / Math.max(aa, 1e-9), 0, 1); }
  else if (t > 1) { t = 1; s = THREE.MathUtils.clamp((bb - c) / Math.max(aa, 1e-9), 0, 1); }
  const p1 = new THREE.Vector3(x, y0, z).addScaledVector(d1, s), p2 = new THREE.Vector3().copy(a).addScaledVector(d2, t);
  return p1.distanceTo(p2);
}
