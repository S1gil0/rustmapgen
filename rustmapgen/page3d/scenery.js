// The scenery the map file stands: cliffs, rocks, ice, power line pylons, substations and tunnel
// entrances, each where the game will stand it, from the models of models3d.py. Two instanced meshes a
// kind: the near model within `near` metres (more for a big piece), the far one while the piece is
// at least `angle` radians across, nothing beyond. Only the far models come with the page; a kind's
// near model is fetched the first time one of its pieces is near, and the far one stands in until
// then. Rocks take the ground's colour on their tops, as the game's rock shader does (snow on rocks
// in the snow, sand in the desert), and are shaded smooth; built things keep their edges.
import * as THREE from 'three';
import { skyFog } from './viewer/sky.js';
import { fetchFar, fetchModel } from './monuments.js';

const TOPS = /* glsl */`
  #include <normal_fragment_maps>
  {
    vec3 sWorldNormal = normalize((vec4(normal, 0.0) * viewMatrix).xyz);
    vec2 sCell = vec2(vScenePos.x - frame.x, -vScenePos.z - frame.y) / (frame.z * (frame.w - 1.0));
    vec3 sGround = texture2D(groundColour, sCell).rgb;
    diffuseColor.rgb = mix(diffuseColor.rgb, sGround, smoothstep(0.55, 0.92, sWorldNormal.y) * topShare);
  }`;

// Pylons, substations, bunkers and bus stops are built things; everything else is rock or ice.
const BUILT = /powerline|power substations|tunnel-entrance|busstop/;

export class Scenery {
  // `ground`: the MapGround, whose colour texture and frame the tops read.
  constructor(fog, ground) {
    this.group = new THREE.Group();
    this.materials = [0.75, 0].map((share) => {
      const material = new THREE.MeshStandardMaterial({ vertexColors: true, flatShading: share === 0, roughness: 0.92, metalness: 0.02, side: THREE.DoubleSide });
      material.onBeforeCompile = (shader) => {
        Object.assign(shader.uniforms, { groundColour: ground.uniforms.groundColour, frame: ground.uniforms.frame, topShare: { value: share } });
        shader.vertexShader = shader.vertexShader
          .replace('#include <common>', '#include <common>\nvarying vec3 vScenePos;')
          .replace('#include <begin_vertex>', '#include <begin_vertex>\nvScenePos = (modelMatrix * instanceMatrix * vec4(transformed, 1.0)).xyz;');
        shader.fragmentShader = shader.fragmentShader
          .replace('#include <common>', '#include <common>\nuniform sampler2D groundColour;\nuniform vec4 frame;\nuniform float topShare;\nvarying vec3 vScenePos;')
          .replace('#include <normal_fragment_maps>', TOPS);
        skyFog(shader, fog);
      };
      material.customProgramCacheKey = () => `map-scenery-${share}`;
      return material;
    });
    this.count = 0;
    this.kinds = [];
    this.missing = [];
    this.last = null;
    this.shown = [0, 0];
    this.triangles = 0;
    this.onChange = () => {};
  }

  // `keys`: 3d_meta.json's scenery_kinds; `bytes`: 3d_scenery.bin; `base`: the folder of the scenery
  // models; `index`: its index.json.
  async load(keys, bytes, base, index) {
    this.base = base;
    const view = new DataView(bytes);
    const count = bytes.byteLength / 44;
    this.count = count;
    this.kind = new Uint16Array(count);
    this.matrices = new Float32Array(count * 16);
    this.centre = new Float32Array(count * 3);
    this.radius = new Float32Array(count);
    const per = new Array(keys.length).fill(0);
    for (let i = 0; i < count; i++) per[(this.kind[i] = view.getUint16(i * 44, true))]++;
    const file = (key, name) => ((index[key] || { levels: [] }).levels.find((level) => level.name === name) || {}).file;
    // A kind with nothing to draw (a swamp of plants and water) has no model and is not asked for.
    const bundled = await fetchFar(base);
    const fars = await Promise.all(keys.map(async (key, k) => {
      if (!per[k] || !file(key, 'far')) { this.missing.push(key); return null; }
      try {
        return (bundled && bundled(key)) || await fetchModel(base + file(key, 'far'));
      } catch (error) {
        this.missing.push(key);
        return null;
      }
    }));
    const matrix = new THREE.Matrix4();
    const position = new THREE.Vector3();
    const turn = new THREE.Quaternion();
    const size = new THREE.Vector3();
    const middle = new THREE.Vector3();
    for (let i = 0; i < count; i++) {
      const at = i * 44;
      position.set(view.getFloat32(at + 4, true), view.getFloat32(at + 8, true), view.getFloat32(at + 12, true));
      turn.set(view.getFloat32(at + 16, true), view.getFloat32(at + 20, true), view.getFloat32(at + 24, true), view.getFloat32(at + 28, true));
      size.set(view.getFloat32(at + 32, true), view.getFloat32(at + 36, true), view.getFloat32(at + 40, true));
      matrix.compose(position, turn, size);
      matrix.toArray(this.matrices, i * 16);
      const model = fars[this.kind[i]];
      if (model) {
        const [x0, y0, z0, x1, y1, z1] = model.box;
        middle.set((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2).applyMatrix4(matrix);
        this.radius[i] = Math.hypot(x1 - x0, y1 - y0, z1 - z0) / 2 * Math.max(Math.abs(size.x), Math.abs(size.y), Math.abs(size.z));
      } else {
        middle.copy(position);
      }
      middle.toArray(this.centre, i * 3);
    }
    this.kinds = fars.map((model, k) => {
      if (!model) return null;
      const built = BUILT.test(keys[k]);
      const kind = { key: keys[k], count: per[k], material: this.materials[built ? 1 : 0], smooth: !built, nearFile: file(keys[k], 'near'), near: null, meshes: [null, null] };
      kind.meshes[1] = this.mesh(kind, model.levels[0], false);
      return kind;
    });
  }

  mesh(kind, level, near) {
    if (kind.smooth) level.geometry.computeVertexNormals();
    const mesh = new THREE.InstancedMesh(level.geometry, kind.material, kind.count);
    mesh.count = 0;
    mesh.frustumCulled = false;
    mesh.castShadow = near;
    mesh.receiveShadow = true;
    mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    mesh.userData.triangles = level.triangles;
    this.group.add(mesh);
    return mesh;
  }

  // Brings in a kind's near model; until it is there, its far model stands in.
  ask(kind) {
    if (kind.near || !kind.nearFile) return;
    kind.near = 'asked';
    fetchModel(this.base + kind.nearFile).then((model) => {
      kind.meshes[0] = this.mesh(kind, model.levels[0], true);
      kind.near = true;
      this.last = null;
      this.onChange(kind.key);
    }, () => { kind.near = 'failed'; });
  }

  // `eye`: the camera's position (viewer space). True when what is drawn changed.
  update(eye, near, angle, above) {
    if (this.last && this.last.distanceTo(eye) < Math.max(4, above * 0.04)) return false;
    this.last = eye.clone();
    const used = this.kinds.map((kind) => (kind ? [0, 0] : null));
    this.shown = [0, 0];
    this.triangles = 0;
    for (let i = 0; i < this.count; i++) {
      const kind = this.kinds[this.kind[i]];
      if (!kind) continue;
      const dx = this.centre[i * 3] - eye.x, dy = this.centre[i * 3 + 1] - eye.y, dz = this.centre[i * 3 + 2] - eye.z;
      const distance = Math.sqrt(dx * dx + dy * dy + dz * dz);
      const r = this.radius[i];
      let level = distance < near + r * 3 ? 0 : r / distance > angle ? 1 : -1;
      if (level < 0) continue;
      if (level === 0 && kind.near !== true) {
        this.ask(kind);
        level = 1;
      }
      const mesh = kind.meshes[level];
      mesh.instanceMatrix.array.set(this.matrices.subarray(i * 16, i * 16 + 16), used[this.kind[i]][level] * 16);
      used[this.kind[i]][level]++;
      this.shown[level]++;
      this.triangles += mesh.userData.triangles;
    }
    this.kinds.forEach((kind, k) => {
      if (!kind) return;
      kind.meshes.forEach((mesh, level) => {
        if (!mesh) return;
        mesh.count = used[k][level];
        mesh.instanceMatrix.needsUpdate = true;
      });
    });
    return true;
  }
}
