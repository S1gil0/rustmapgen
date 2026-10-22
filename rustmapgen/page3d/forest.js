// Trees around where the camera looks: the scattered trees of the bake (trees.bin), drawn with
// low-poly shapes of our own (viewer/nature.js), one instanced mesh a kind. Only
// those within `radius` of the focus are drawn; the set is rebuilt when the focus has moved a fifth
// of that. Far away the ground's own forest colour stands in for them.
import * as THREE from 'three';
import { Nature } from './viewer/nature.js';
import { hash2 } from './viewer/noise.js';

const BUCKET_M = 128;
const UP = new THREE.Vector3(0, 1, 0);

export class Forest {
  constructor(fog, bytes, kinds, size, heightAt) {
    this.nature = new Nature(fog);
    this.group = this.nature.group;
    this.size = size;
    this.kinds = kinds;
    const count = bytes.byteLength / 8;
    const view = new DataView(bytes);
    this.x = new Float32Array(count);
    this.z = new Float32Array(count);
    this.y = new Float32Array(count);
    this.kind = new Uint8Array(count);
    this.turn = new Float32Array(count);
    this.scale = new Float32Array(count);
    this.side = Math.ceil(size / BUCKET_M);
    const buckets = Array.from({ length: this.side * this.side }, () => []);
    const perKind = new Array(kinds.length).fill(0);
    for (let t = 0; t < count; t++) {
      const x = view.getUint16(t * 8, true) / 65535 * size - size / 2;
      const z = view.getUint16(t * 8 + 2, true) / 65535 * size - size / 2;
      this.x[t] = x;
      this.z[t] = z;
      this.y[t] = heightAt(x, z) - 0.3;
      this.kind[t] = view.getUint8(t * 8 + 4);
      this.turn[t] = view.getUint8(t * 8 + 5) / 255 * Math.PI * 2;
      this.scale[t] = view.getUint8(t * 8 + 6) / 100;
      perKind[this.kind[t]]++;
      buckets[this.bucket(x, z)].push(t);
    }
    this.buckets = buckets.map((list) => Uint32Array.from(list));
    this.count = count;
    this.meshes = kinds.map((kind, k) => {
      if (!perKind[k]) return null;
      const mesh = new THREE.InstancedMesh(this.nature.geometry(kind), this.nature.material, perKind[k]);
      mesh.count = 0;
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      mesh.frustumCulled = false;
      mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
      this.group.add(mesh);
      return mesh;
    });
    this.trianglesOf = this.meshes.map((mesh) => (!mesh ? 0 : (mesh.geometry.index || mesh.geometry.getAttribute('position')).count / 3));
    this.focus = null;
    this.radius = 0;
    this.shown = 0;
    this.triangles = 0;
  }

  bucket(x, z) {
    const i = Math.min(this.side - 1, Math.max(0, Math.floor((x + this.size / 2) / BUCKET_M)));
    const j = Math.min(this.side - 1, Math.max(0, Math.floor((z + this.size / 2) / BUCKET_M)));
    return j * this.side + i;
  }

  // Game position of the focus; true when the trees drawn changed. Past `budget` triangles the
  // farthest trees are left out.
  update(x, z, radius, budget) {
    if (this.focus && this.radius === radius && Math.hypot(x - this.focus[0], z - this.focus[1]) < radius / 5) return false;
    this.focus = [x, z];
    this.radius = radius;
    const near = [];
    const reach = Math.ceil(radius / BUCKET_M);
    const ci = Math.floor((x + this.size / 2) / BUCKET_M);
    const cj = Math.floor((z + this.size / 2) / BUCKET_M);
    const far = radius * radius;
    let triangles = 0;
    for (let j = Math.max(0, cj - reach); j <= Math.min(this.side - 1, cj + reach); j++) {
      for (let i = Math.max(0, ci - reach); i <= Math.min(this.side - 1, ci + reach); i++) {
        for (const t of this.buckets[j * this.side + i]) {
          const dx = this.x[t] - x, dz = this.z[t] - z;
          const d = dx * dx + dz * dz;
          if (d > far || !this.meshes[this.kind[t]]) continue;
          near.push([d, t]);
          triangles += this.trianglesOf[this.kind[t]];
        }
      }
    }
    if (triangles > budget) near.sort((a, b) => a[0] - b[0]);
    const used = new Array(this.kinds.length).fill(0);
    const matrix = new THREE.Matrix4();
    const position = new THREE.Vector3();
    const turn = new THREE.Quaternion();
    const scale = new THREE.Vector3();
    const tint = new THREE.Color();
    let spent = 0;
    for (const [, t] of near) {
      const k = this.kind[t];
      spent += this.trianglesOf[k];
      if (spent > budget) break;
      const mesh = this.meshes[k];
      position.set(this.x[t], this.y[t], -this.z[t]);
      turn.setFromAxisAngle(UP, this.turn[t]);
      scale.setScalar(this.scale[t]);
      mesh.setMatrixAt(used[k], matrix.compose(position, turn, scale));
      const shade = 0.84 + hash2(Math.round(this.x[t] * 10), Math.round(this.z[t] * 10), 3) * 0.32;
      const warm = hash2(Math.round(this.x[t] * 10), Math.round(this.z[t] * 10), 8) - 0.5;
      mesh.setColorAt(used[k], tint.setRGB(shade * (1 + warm * 0.14), shade, shade * (1 - warm * 0.2)));
      used[k]++;
    }
    this.shown = 0;
    this.triangles = 0;
    this.meshes.forEach((mesh, k) => {
      if (!mesh) return;
      mesh.count = used[k];
      mesh.instanceMatrix.needsUpdate = true;
      if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
      this.shown += used[k];
      this.triangles += used[k] * this.trianglesOf[k];
    });
    return true;
  }

  hide() {
    if (!this.focus && !this.shown) return false;
    this.meshes.forEach((mesh) => { if (mesh) mesh.count = 0; });
    this.focus = null;
    this.shown = 0;
    this.triangles = 0;
    return true;
  }
}
