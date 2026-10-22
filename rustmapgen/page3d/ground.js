// The whole map's ground (prototype): the map file's height grid drawn in tiles of 64 cells, each
// at the detail its distance asks for. One shared mesh a level of detail, instanced over the tiles
// that use it, so the ground is a handful of draw calls. The vertex shader reads the heights from a
// float texture; the light comes from a normal texture at full detail, so a far tile drawn coarse
// still shows every slope. Skirts under the tile edges hide the cracks between levels.
// The sea, rivers and lakes: the sea reads its depth from the normal texture's alpha.
import * as THREE from 'three';
import { fbmField, noiseTexture, normalsFromHeight } from './viewer/noise.js';
import { skyFog } from './viewer/sky.js';

export const TILE = 64;          // cells a tile side
export const LEVELS = 7;         // 64, 32, 16, 8, 4, 2 and 1 cells a tile side
const SEA_DEPTH_SCALE = 12;      // metres of sea the normal texture's alpha holds

const GROUND_VERTEX = /* glsl */`
  vec4 gPlace = instanceMatrix * vec4(position.x, 0.0, position.z, 1.0);
  ivec2 gSample = ivec2(clamp(round((gPlace.x - frame.x) / frame.z), 0.0, frame.w - 1.0),
                        clamp(round((-gPlace.z - frame.y) / frame.z), 0.0, frame.w - 1.0));
  float gHeight = texelFetch(heightMap, gSample, 0).r - position.y;
  vec3 transformed = vec3(position.x, gHeight, position.z);
  vGroundPos = vec3(gPlace.x, gHeight, gPlace.z);`;

// The colour, then the detail of viewer/terrain.js GROUND_COLOUR, with the makeup of the ground
// (plants, rock, smooth) from the mix texture instead of the vertices.
const GROUND_COLOUR = /* glsl */`
  vec2 gCell = vec2(vGroundPos.x - frame.x, -vGroundPos.z - frame.y) / (frame.z * (frame.w - 1.0));
  vec2 gCorner = (gCell * (frame.w - 1.0) + 0.5) / frame.w;
  vec3 gWorldNormal = normalize(texture2D(groundNormal, gCorner).xyz * 2.0 - 1.0);
  diffuseColor.rgb = texture2D(groundColour, gCell).rgb;
  vec3 gMix = texture2D(groundMix, gCell).rgb;
  // Steep ground shows the rock under it.
  float gSteep = length(gWorldNormal.xz) / max(gWorldNormal.y, 0.05);
  float gBare = clamp((gSteep - 0.8) / 0.7, 0.0, 1.0);
  diffuseColor.rgb = mix(diffuseColor.rgb, rockColour, gBare * 0.6);
  gMix = vec3(gMix.x * (1.0 - gBare), max(gMix.y, gBare), gMix.z * (1.0 - gBare));
  // Wet at the waterline, then sinking into the colour of deep water.
  float gWet = clamp((0.6 - vGroundPos.y) / 0.6, 0.0, 1.0);
  float gDeep = clamp(-vGroundPos.y / 5.0, 0.0, 1.0);
  diffuseColor.rgb = mix(diffuseColor.rgb * (1.0 - gWet * 0.3), deepColour, gDeep * 0.8);
  gMix.x *= 1.0 - gWet;
  vec2 gp = vGroundPos.xz;
  float gFine = texture2D(groundNoise, gp * 0.37).b;
  float gGrit = texture2D(groundNoise, gp * 0.093).g;
  float gMid = texture2D(groundNoise, gp * 0.021).r;
  float gMacro = texture2D(groundNoise, gp * 0.0047).a;
  float gNear = 1.0 - smoothstep(45.0, 210.0, vFogDepth);
  float gShade = ((gFine - 0.5) * (0.4 + 0.3 * gMix.y) + (gGrit - 0.5) * 0.5) * mix(0.3, 1.0, gNear) + (gMid - 0.5) * 0.4;
  diffuseColor.rgb *= 1.0 + gShade * (1.0 - 0.72 * gMix.z);
  float gTuft = smoothstep(0.3, 0.72, texture2D(groundNoise, gp * 0.19 + 0.37).r);
  diffuseColor.rgb *= mix(1.0, mix(0.7, 1.14, gTuft), gMix.x * gNear);
  diffuseColor.rgb = mix(diffuseColor.rgb, diffuseColor.rgb * vec3(1.34, 1.1, 0.6), smoothstep(0.4, 0.72, gMacro) * gMix.x * 0.7);
  diffuseColor.rgb = mix(diffuseColor.rgb, diffuseColor.rgb * vec3(0.74, 0.86, 0.7), smoothstep(0.52, 0.8, gMid) * gMix.x * 0.6);
  diffuseColor.rgb *= 1.0 - gMix.y * (1.0 - smoothstep(0.3, 0.56, gGrit)) * 0.26;
  // Where the game does not let a base be built (nobuild.js): red, with a line at the edge.
  vec2 gNoBuildAt = (vec2(vGroundPos.x - noBuildFrame.x, -vGroundPos.z - noBuildFrame.y) / noBuildFrame.z + 0.5) / noBuildFrame.w;
  float gNoBuild = texture2D(noBuild, gNoBuildAt).r;
  float gNoBuildEdge = 1.0 - smoothstep(0.0, fwidth(gNoBuild) * 1.5 + 1e-4, abs(gNoBuild - 0.5));
  diffuseColor.rgb = mix(diffuseColor.rgb, vec3(0.86, 0.1, 0.06), clamp(step(0.5, gNoBuild) * 0.4 + gNoBuildEdge * 0.7, 0.0, 0.9) * noBuildOn);`;

const GROUND_NORMAL = /* glsl */`
  float faceDirection = 1.0;
  vec3 normal = normalize((viewMatrix * vec4(gWorldNormal, 0.0)).xyz);
  vec3 nonPerturbedNormal = normal;`;

// viewer/terrain.js GROUND_NORMAL: fine bumps, strongest on rock, none on snow and sand.
const GROUND_BUMPS = /* glsl */`
  {
    float gBump = (gFine * 0.035 + gGrit * 0.11) * (0.55 + gMix.y * 1.1) * (1.0 - 0.6 * gMix.z) * gNear;
    vec3 gPx = dFdx(-vViewPosition);
    vec3 gPy = dFdy(-vViewPosition);
    vec3 gR1 = cross(gPy, normal);
    vec3 gR2 = cross(normal, gPx);
    float gDet = dot(gPx, gR1);
    vec3 gGrad = sign(gDet) * (dFdx(gBump) * gR1 + dFdy(gBump) * gR2);
    normal = normalize(abs(gDet) * normal - gGrad);
  }`;

// viewer/terrain.js WATER_COLOUR and WATER_NORMAL; the sea's depth comes from the ground's normal
// texture, and past the square the sea is open water.
const WATER_COLOUR = /* glsl */`
  #include <color_fragment>
  #ifdef RIVER
  float wDepth = vWaterDepth;
  #else
  vec2 wCell = vec2(vWaterPos.x - frame.x, -vWaterPos.z - frame.y) / (frame.z * (frame.w - 1.0));
  vec2 wCorner = (wCell * (frame.w - 1.0) + 0.5) / frame.w;
  vec2 wEdge = min(wCell, 1.0 - wCell);
  float wDepth = mix(${SEA_DEPTH_SCALE.toFixed(1)}, texture2D(groundNormal, wCorner).a * ${SEA_DEPTH_SCALE.toFixed(1)}, step(0.0, min(wEdge.x, wEdge.y)));
  #endif
  float wShallow = 1.0 - smoothstep(0.0, 3.6, wDepth);
  diffuseColor.rgb = mix(vec3(0.010, 0.060, 0.092), vec3(0.045, 0.300, 0.300), wShallow * wShallow);
  float wSurf = texture2D(foamMap, vWaterPos.xz * 0.23 + waveTime * vec2(0.014, 0.009)).g;
  float wFoam = (1.0 - smoothstep(0.02, 0.16, wDepth)) * smoothstep(0.34, 0.6, wSurf);
  diffuseColor.rgb = mix(diffuseColor.rgb, vec3(0.8, 0.84, 0.84), wFoam * 0.75);
  diffuseColor.a = max(mix(0.95, 0.6, wShallow * wShallow), wFoam) * smoothstep(0.0, 0.1, wDepth);`;

const WATER_NORMAL = /* glsl */`
  {
    vec2 wA = texture2D(waveMap, vWaterPos.xz * 0.043 + waveTime * vec2(0.011, 0.006)).xy * 2.0 - 1.0;
    vec2 wB = texture2D(waveMap, vWaterPos.zx * 0.117 + waveTime * vec2(-0.015, 0.01)).xy * 2.0 - 1.0;
    float wCalm = 0.34 / (1.0 + length(vViewPosition) * 0.008);
    vec3 wN = normalize(vec3((wA.x + wB.x * 0.6) * wCalm, 1.0, (wA.y + wB.y * 0.6) * wCalm));
    normal = normalize(mat3(viewMatrix) * wN);
  }`;

// One tile's mesh at a level: (cells + 1)^2 vertices in metres from the tile's south-west corner
// (viewer space: z runs north as negative), and a skirt hanging from each edge. A vertex's y is how
// far below the ground it hangs: 0 on the surface.
function tileGeometry(cells, step, skirt) {
  const position = [];
  const index = [];
  const side = cells + 1;
  for (let j = 0; j <= cells; j++) for (let i = 0; i <= cells; i++) position.push(i * step, 0, -j * step);
  for (let j = 0; j < cells; j++) {
    for (let i = 0; i < cells; i++) {
      const a = j * side + i, b = a + 1, c = a + side, d = c + 1;
      index.push(a, c, b, b, c, d);
    }
  }
  const edges = [[], [], [], []];
  for (let k = 0; k <= cells; k++) {
    edges[0].push(k);                        // south
    edges[1].push(cells * side + k);         // north
    edges[2].push(k * side);                 // west
    edges[3].push(k * side + cells);         // east
  }
  for (const edge of edges) {
    const first = position.length / 3;
    for (const at of edge) position.push(position[at * 3], skirt, position[at * 3 + 2]);
    for (let k = 0; k < edge.length - 1; k++) {
      const a = edge[k], b = edge[k + 1], c = first + k, d = first + k + 1;
      index.push(a, b, c, b, d, c);
    }
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(position, 3));
  geometry.setIndex(index);
  geometry.boundingSphere = new THREE.Sphere(new THREE.Vector3(), 1e9);
  return geometry;
}

export class MapGround {
  // `map`: { size, n, heights (Float32Array n*n, row z then x from the south-west), rivers
  // (Float32Array, metres of water, NaN where none) }; `colour`, `mix`: the ground textures.
  constructor(renderer, fog, map, colour, mix) {
    this.map = map;
    const { size, n, heights } = map;
    this.step = size / (n - 1);
    this.x0 = -size / 2;
    this.z0 = -size / 2;
    this.tiles = (n - 1) / TILE;
    this.group = new THREE.Group();

    const heightMap = new THREE.DataTexture(heights, n, n, THREE.RedFormat, THREE.FloatType);
    heightMap.magFilter = heightMap.minFilter = THREE.NearestFilter;
    heightMap.needsUpdate = true;
    this.normalMap = this.normals();
    for (const texture of [colour, mix]) {
      texture.wrapS = texture.wrapT = THREE.ClampToEdgeWrapping;
      texture.anisotropy = Math.min(8, renderer.capabilities.getMaxAnisotropy());
      texture.flipY = false;
      texture.needsUpdate = true;
    }
    colour.colorSpace = THREE.SRGBColorSpace;
    mix.colorSpace = THREE.NoColorSpace;
    this.textures = [heightMap, this.normalMap, colour, mix];
    const frame = new THREE.Vector4(this.x0, this.z0, this.step, n);
    this.uniforms = {
      heightMap: { value: heightMap }, groundNormal: { value: this.normalMap }, groundColour: { value: colour }, groundMix: { value: mix },
      groundNoise: { value: noiseTexture() }, frame: { value: frame },
      rockColour: { value: new THREE.Color('#827e76') }, deepColour: { value: new THREE.Color('#17363b') },
      // The no-build mask (nobuild.js) and where it lies: x, z of its first texel, metres a texel, texels a side.
      noBuild: { value: new THREE.DataTexture(new Uint8Array(1), 1, 1, THREE.RedFormat) }, noBuildFrame: { value: new THREE.Vector4(0, 0, 1, 1) },
      noBuildOn: { value: 0 },
    };
    this.uniforms.noBuild.value.needsUpdate = true;
    this.material = new THREE.MeshStandardMaterial({ roughness: 0.94, metalness: 0, side: THREE.DoubleSide });
    this.material.onBeforeCompile = (shader) => {
      Object.assign(shader.uniforms, this.uniforms);
      shader.vertexShader = shader.vertexShader
        .replace('#include <common>', '#include <common>\nuniform sampler2D heightMap;\nuniform vec4 frame;\nvarying vec3 vGroundPos;')
        .replace('#include <beginnormal_vertex>', 'vec3 objectNormal = vec3(0.0, 1.0, 0.0);')
        .replace('#include <begin_vertex>', GROUND_VERTEX);
      shader.fragmentShader = shader.fragmentShader
        .replace('#include <common>', '#include <common>\nuniform sampler2D groundNormal;\nuniform sampler2D groundColour;\nuniform sampler2D groundMix;\nuniform sampler2D groundNoise;\nuniform vec4 frame;\nuniform vec3 rockColour;\nuniform vec3 deepColour;\nuniform sampler2D noBuild;\nuniform vec4 noBuildFrame;\nuniform float noBuildOn;\nvarying vec3 vGroundPos;')
        .replace('#include <color_fragment>', GROUND_COLOUR)
        .replace('#include <normal_fragment_begin>', GROUND_NORMAL)
        .replace('#include <normal_fragment_maps>', GROUND_BUMPS);
      skyFog(shader, fog);
    };
    this.material.customProgramCacheKey = () => 'map-ground';

    // Tiles: the lowest and highest ground of each, for culling and distances.
    const count = this.tiles * this.tiles;
    this.low = new Float32Array(count);
    this.high = new Float32Array(count);
    for (let tj = 0; tj < this.tiles; tj++) {
      for (let ti = 0; ti < this.tiles; ti++) {
        let lo = Infinity, hi = -Infinity;
        for (let j = tj * TILE; j <= (tj + 1) * TILE; j++) {
          for (let i = ti * TILE; i <= (ti + 1) * TILE; i++) {
            const h = heights[j * n + i];
            if (h < lo) lo = h;
            if (h > hi) hi = h;
          }
        }
        this.low[tj * this.tiles + ti] = lo;
        this.high[tj * this.tiles + ti] = hi;
      }
    }
    this.levels = [];
    for (let level = 0; level < LEVELS; level++) {
      const cells = TILE >> level;
      const cellM = this.step * (1 << level);
      const mesh = new THREE.InstancedMesh(tileGeometry(cells, cellM, Math.max(4, cellM * 2)), this.material, count);
      mesh.frustumCulled = false;
      mesh.receiveShadow = true;
      mesh.count = 0;
      mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
      this.levels.push(mesh);
      this.group.add(mesh);
    }
    this.used = new Array(LEVELS).fill(0);
    this.box = new THREE.Box3();
    this.frustum = new THREE.Frustum();
    this.matrix = new THREE.Matrix4();
    this.point = new THREE.Vector3();

    this.buildWater(renderer, fog);
    this.buildSeaFloor(fog);
  }

  // Normals of the ground at every sample (viewer space) and, in alpha, how deep the sea is there.
  normals() {
    const { n, heights } = this.map;
    const data = new Uint8Array(n * n * 4);
    const twice = 2 * this.step;
    for (let j = 0; j < n; j++) {
      const down = Math.max(0, j - 1) * n, up = Math.min(n - 1, j + 1) * n, row = j * n;
      for (let i = 0; i < n; i++) {
        const left = Math.max(0, i - 1), right = Math.min(n - 1, i + 1);
        const dx = (heights[row + right] - heights[row + left]) / twice;
        const dz = (heights[up + i] - heights[down + i]) / twice;
        // Game space (-dx, 1, -dz); viewer space mirrors z.
        const length = Math.hypot(dx, 1, dz);
        const at = (row + i) * 4;
        data[at] = Math.round((-dx / length * 0.5 + 0.5) * 255);
        data[at + 1] = Math.round((1 / length * 0.5 + 0.5) * 255);
        data[at + 2] = Math.round((dz / length * 0.5 + 0.5) * 255);
        data[at + 3] = Math.round(Math.min(1, Math.max(0, -heights[row + i] / SEA_DEPTH_SCALE)) * 255);
      }
    }
    const texture = new THREE.DataTexture(data, n, n, THREE.RGBAFormat);
    texture.magFilter = THREE.LinearFilter;
    texture.minFilter = THREE.LinearMipmapLinearFilter;
    texture.generateMipmaps = true;
    texture.needsUpdate = true;
    return texture;
  }

  heightAt(x, z) {
    const { n, heights } = this.map;
    const fx = Math.min(n - 1.001, Math.max(0, (x - this.x0) / this.step));
    const fz = Math.min(n - 1.001, Math.max(0, (z - this.z0) / this.step));
    const i = Math.floor(fx), j = Math.floor(fz), tx = fx - i, tz = fz - j;
    const a = heights[j * n + i] + (heights[j * n + i + 1] - heights[j * n + i]) * tx;
    const b = heights[(j + 1) * n + i] + (heights[(j + 1) * n + i + 1] - heights[(j + 1) * n + i]) * tx;
    return a + (b - a) * tz;
  }

  // Choose every tile's level for this camera: a cell may cover about `pixels` pixels of the
  // screen. Tiles out of view are left out.
  update(camera, viewportHeight, pixels) {
    this.frustum.setFromProjectionMatrix(this.matrix.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse));
    const focal = viewportHeight / (2 * Math.tan(THREE.MathUtils.degToRad(camera.fov) / 2));
    const tileM = TILE * this.step;
    this.used.fill(0);
    const eye = camera.position;
    for (let tj = 0; tj < this.tiles; tj++) {
      for (let ti = 0; ti < this.tiles; ti++) {
        const t = tj * this.tiles + ti;
        const x = this.x0 + ti * tileM;
        const z = this.z0 + tj * tileM;
        this.box.min.set(x, this.low[t] - 4, -(z + tileM));
        this.box.max.set(x + tileM, this.high[t], -z);
        if (!this.frustum.intersectsBox(this.box)) continue;
        const distance = Math.max(1, this.box.distanceToPoint(eye));
        const wanted = pixels * distance / focal / this.step;
        const level = Math.min(LEVELS - 1, Math.max(0, Math.floor(Math.log2(Math.max(1, wanted)))));
        const mesh = this.levels[level];
        this.matrix.makeTranslation(x, 0, -z);
        mesh.setMatrixAt(this.used[level]++, this.matrix);
      }
    }
    let triangles = 0;
    this.levels.forEach((mesh, level) => {
      mesh.count = this.used[level];
      mesh.instanceMatrix.needsUpdate = true;
      triangles += this.used[level] * 2 * (TILE >> level) ** 2;
    });
    return { tiles: [...this.used], triangles };
  }

  buildWater(renderer, fog) {
    const size = 128;
    const waves = fbmField(size, 8, 4, 71);
    const normals = new THREE.DataTexture(normalsFromHeight(waves, size, 5), size, size, THREE.RGBAFormat);
    normals.wrapS = normals.wrapT = THREE.RepeatWrapping;
    normals.magFilter = THREE.LinearFilter;
    normals.minFilter = THREE.LinearMipmapLinearFilter;
    normals.generateMipmaps = true;
    normals.anisotropy = Math.min(4, renderer.capabilities.getMaxAnisotropy());
    normals.needsUpdate = true;
    this.textures.push(normals);
    this.waterUniforms = { waveMap: { value: normals }, waveTime: { value: 0 }, foamMap: { value: noiseTexture() },
      groundNormal: this.uniforms.groundNormal, frame: this.uniforms.frame };
    const water = (river) => {
      const material = new THREE.MeshStandardMaterial({ color: '#ffffff', roughness: 0.09, metalness: 0, transparent: true, side: river ? THREE.DoubleSide : THREE.FrontSide });
      material.onBeforeCompile = (shader) => {
        Object.assign(shader.uniforms, this.waterUniforms);
        shader.vertexShader = shader.vertexShader
          .replace('#include <common>', `#include <common>\nvarying vec3 vWaterPos;${river ? '\nattribute float waterDepth;\nvarying float vWaterDepth;' : ''}`)
          .replace('#include <begin_vertex>', `#include <begin_vertex>\nvWaterPos = (modelMatrix * vec4(transformed, 1.0)).xyz;${river ? '\nvWaterDepth = waterDepth;' : ''}`);
        shader.fragmentShader = shader.fragmentShader
          .replace('#include <common>', `#include <common>\n${river ? '#define RIVER\nvarying float vWaterDepth;\n' : ''}uniform sampler2D waveMap;\nuniform sampler2D foamMap;\nuniform sampler2D groundNormal;\nuniform vec4 frame;\nuniform float waveTime;\nvarying vec3 vWaterPos;`)
          .replace('#include <color_fragment>', WATER_COLOUR)
          .replace('#include <normal_fragment_maps>', WATER_NORMAL);
        skyFog(shader, fog);
      };
      material.customProgramCacheKey = () => (river ? 'map-river' : 'map-sea');
      return material;
    };
    this.seaMaterial = water(false);
    this.riverMaterial = water(true);
    const sea = new THREE.PlaneGeometry(this.map.size * 12, this.map.size * 12);
    sea.rotateX(-Math.PI / 2);
    this.sea = new THREE.Mesh(sea, this.seaMaterial);
    this.sea.receiveShadow = true;
    this.sea.renderOrder = -50;
    this.group.add(this.sea);
    this.buildRivers();
  }

  // Rivers and lakes over the samples that hold one, as viewer/terrain.js buildRivers does on a patch.
  buildRivers() {
    const { n, heights, rivers } = this.map;
    if (!rivers) return;
    const where = new Int32Array(n * n).fill(-1);
    const position = [];
    const depth = [];
    for (let j = 0; j < n; j++) {
      for (let i = 0; i < n; i++) {
        const at = j * n + i;
        if (Number.isNaN(rivers[at])) continue;
        where[at] = depth.length;
        position.push(this.x0 + i * this.step, heights[at] + rivers[at], -(this.z0 + j * this.step));
        depth.push(rivers[at]);
      }
    }
    const index = [];
    const face = (a, b, c) => { if (depth[a] > 0 || depth[b] > 0 || depth[c] > 0) index.push(a, b, c); };
    for (let j = 0; j < n - 1; j++) {
      for (let i = 0; i < n - 1; i++) {
        const a = where[j * n + i], b = where[j * n + i + 1], d = where[(j + 1) * n + i], e = where[(j + 1) * n + i + 1];
        const missing = (a < 0) + (b < 0) + (d < 0) + (e < 0);
        if (missing === 0) { face(a, b, d); face(b, e, d); } else if (missing === 1) {
          if (e < 0) face(a, b, d); else if (a < 0) face(b, e, d); else if (b < 0) face(a, e, d); else face(a, b, e);
        }
      }
    }
    if (!index.length) return;
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(position, 3));
    geometry.setAttribute('waterDepth', new THREE.Float32BufferAttribute(depth, 1));
    geometry.setIndex(index);
    geometry.computeVertexNormals();
    const mesh = new THREE.Mesh(geometry, this.riverMaterial);
    mesh.receiveShadow = true;
    mesh.renderOrder = -49;
    this.group.add(mesh);
    this.riverTriangles = index.length / 3;
  }

  // Past the square: a sea floor far under the waves, so open water never shows the sky through it.
  buildSeaFloor(fog) {
    const material = new THREE.MeshStandardMaterial({ color: '#3d4b45', roughness: 1, metalness: 0 });
    material.onBeforeCompile = (shader) => skyFog(shader, fog);
    material.customProgramCacheKey = () => 'map-sea-floor';
    const floor = new THREE.PlaneGeometry(this.map.size * 12, this.map.size * 12);
    floor.rotateX(-Math.PI / 2);
    const mesh = new THREE.Mesh(floor, material);
    mesh.position.y = Math.min(-30, this.map.lowest - 2);
    this.group.add(mesh);
  }

  tick(seconds) {
    this.waterUniforms.waveTime.value = seconds;
  }
}
