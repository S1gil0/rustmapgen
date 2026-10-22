// Seeded random numbers and tiling noise, shared by the sky, the ground and the procedural
// materials. Everything here is deterministic: the same base always looks the same.
import * as THREE from 'three';

export function rng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// A number in [0, 1) from two integers: stable per-instance variation without storing anything.
export function hash2(x, y, seed = 0) {
  let h = (Math.imul(x | 0, 374761393) + Math.imul(y | 0, 668265263) + Math.imul(seed | 0, 2147483647)) | 0;
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
}

// Value noise that repeats: `cells` random values per side, smoothly interpolated to `size` px.
export function tileNoise(size, cells, seed) {
  const random = rng(seed);
  const lattice = new Float32Array(cells * cells);
  for (let i = 0; i < lattice.length; i++) lattice[i] = random();
  const out = new Float32Array(size * size);
  const step = cells / size;
  for (let y = 0; y < size; y++) {
    const fy = y * step;
    const y0 = Math.floor(fy);
    const ty = fy - y0;
    const sy = ty * ty * (3 - 2 * ty);
    const r0 = (y0 % cells) * cells;
    const r1 = ((y0 + 1) % cells) * cells;
    for (let x = 0; x < size; x++) {
      const fx = x * step;
      const x0 = Math.floor(fx);
      const tx = fx - x0;
      const sx = tx * tx * (3 - 2 * tx);
      const c0 = x0 % cells;
      const c1 = (x0 + 1) % cells;
      const top = lattice[r0 + c0] + (lattice[r0 + c1] - lattice[r0 + c0]) * sx;
      const bottom = lattice[r1 + c0] + (lattice[r1 + c1] - lattice[r1 + c0]) * sx;
      out[y * size + x] = top + (bottom - top) * sy;
    }
  }
  return out;
}

// Several octaves of tiling noise, normalised to [0, 1].
export function fbmField(size, firstCells, octaves, seed, gain = 0.5) {
  const out = new Float32Array(size * size);
  let amplitude = 1;
  let total = 0;
  let cells = firstCells;
  for (let o = 0; o < octaves && cells <= size / 2; o++) {
    const layer = tileNoise(size, cells, seed + o * 101);
    for (let i = 0; i < out.length; i++) out[i] += layer[i] * amplitude;
    total += amplitude;
    amplitude *= gain;
    cells *= 2;
  }
  let low = Infinity;
  let high = -Infinity;
  for (let i = 0; i < out.length; i++) {
    out[i] /= total;
    if (out[i] < low) low = out[i];
    if (out[i] > high) high = out[i];
  }
  const span = high - low || 1;
  for (let i = 0; i < out.length; i++) out[i] = (out[i] - low) / span;
  return out;
}

let shared = null;

// One 256 px RGBA texture used everywhere a shader wants noise:
// R broad billows, G a second independent field, B fine grain, A very broad patches.
export function noiseTexture() {
  if (shared) return shared;
  const size = 256;
  const r = fbmField(size, 4, 5, 11);
  const g = fbmField(size, 8, 4, 23);
  const b = fbmField(size, 32, 2, 37);
  const a = fbmField(size, 2, 3, 53);
  const data = new Uint8Array(size * size * 4);
  for (let i = 0; i < size * size; i++) {
    data[i * 4] = Math.round(r[i] * 255);
    data[i * 4 + 1] = Math.round(g[i] * 255);
    data[i * 4 + 2] = Math.round(b[i] * 255);
    data[i * 4 + 3] = Math.round(a[i] * 255);
  }
  shared = new THREE.DataTexture(data, size, size, THREE.RGBAFormat);
  shared.wrapS = shared.wrapT = THREE.RepeatWrapping;
  shared.magFilter = THREE.LinearFilter;
  shared.minFilter = THREE.LinearMipmapLinearFilter;
  shared.generateMipmaps = true;
  shared.colorSpace = THREE.NoColorSpace;
  shared.needsUpdate = true;
  return shared;
}

// A tangent-space normal map from a height field (Float32Array, size*size, repeating).
export function normalsFromHeight(height, size, strength) {
  const data = new Uint8Array(size * size * 4);
  for (let y = 0; y < size; y++) {
    const up = ((y + size - 1) % size) * size;
    const down = ((y + 1) % size) * size;
    for (let x = 0; x < size; x++) {
      const left = (x + size - 1) % size;
      const right = (x + 1) % size;
      const dx = (height[y * size + right] - height[y * size + left]) * strength;
      const dy = (height[down + x] - height[up + x]) * strength;
      const inv = 1 / Math.hypot(dx, dy, 1);
      const at = (y * size + x) * 4;
      data[at] = Math.round((-dx * inv * 0.5 + 0.5) * 255);
      // Rows of a DataTexture run with +v, so no flip here.
      data[at + 1] = Math.round((-dy * inv * 0.5 + 0.5) * 255);
      data[at + 2] = Math.round((inv * 0.5 + 0.5) * 255);
      data[at + 3] = 255;
    }
  }
  return data;
}
