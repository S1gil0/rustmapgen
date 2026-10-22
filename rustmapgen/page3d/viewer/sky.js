// The environment of a scene: the atmosphere (three's Sky addon), a drifting cloud layer, the
// sun or the moon as the one shadow-casting light, image-based lighting taken from that same
// sky, and a distance fog that fades into the horizon colour of the direction you look in.
import * as THREE from 'three';
import { Sky } from 'three/addons/objects/Sky.js';
import { noiseTexture } from './noise.js';

const RING = 16;
const ATMOSPHERE = { turbidity: 2.4, rayleigh: 1.15, mieCoefficient: 0.0038, mieDirectionalG: 0.83 };
// Where the sun travels: a summer day at a low latitude, so noon is high but never overhead.
const LATITUDE = 32 * Math.PI / 180;
const DECLINATION = 14 * Math.PI / 180;
const SUN_INTENSITY = 8.2;
const MOON_INTENSITY = 1.5;
// The Sky addon's colours come out pale once tone mapped; this pulls them apart again. The
// same grade is applied in its shader and in the JavaScript copy of its formulas below.
const SKY_SATURATION = 1.8;
const DAY_EXPOSURE = 0.3;
const NIGHT_ZENITH = [0.012, 0.022, 0.058];
const NIGHT_HORIZON = [0.03, 0.05, 0.1];

const WHITE = new THREE.Color(1, 1, 1);
const TEMP = new THREE.Color();

const clamp = (value, low, high) => Math.min(high, Math.max(low, value));
const smooth = (low, high, value) => {
  const t = clamp((value - low) / (high - low), 0, 1);
  return t * t * (3 - 2 * t);
};

// ---- The Sky addon's formulas in JavaScript, to know the colour of the horizon without
// reading pixels back. Kept in the same order as the shader (vendor/Sky.js).
const TOTAL_RAYLEIGH = [5.804542996261093e-6, 1.3562911419845635e-5, 3.0265902468824876e-5];
const MIE_CONST = [1.8399918514433978e14, 2.7798023919660528e14, 4.0790479543861094e14];

function sunEnergy(zenithCos) {
  const cutoff = 1.6110731556870734;
  return 1000 * Math.max(0, 1 - Math.exp(-((cutoff - Math.acos(clamp(zenithCos, -1, 1))) / 1.5)));
}

function skyRadiance(x, y, z, sun, out) {
  const energy = sunEnergy(sun.y);
  const up = Math.max(0, y);
  const zenith = Math.acos(up);
  const inverse = 1 / (Math.cos(zenith) + 0.15 * Math.pow(93.885 - zenith * 180 / Math.PI, -1.253));
  const cosTheta = x * sun.x + y * sun.y + z * sun.z;
  const rayleighPhase = 0.05968310365946075 * (1 + Math.pow(cosTheta * 0.5 + 0.5, 2));
  const g = ATMOSPHERE.mieDirectionalG;
  const miePhase = 0.07957747154594767 * ((1 - g * g) / Math.pow(1 - 2 * g * cosTheta + g * g, 1.5));
  const dusk = clamp(Math.pow(1 - sun.y, 5), 0, 1);
  for (let c = 0; c < 3; c++) {
    const betaR = TOTAL_RAYLEIGH[c] * ATMOSPHERE.rayleigh;
    const betaM = 0.434 * (0.2 * ATMOSPHERE.turbidity) * 10e-18 * MIE_CONST[c] * ATMOSPHERE.mieCoefficient;
    const extinction = Math.exp(-(betaR * 8400 * inverse + betaM * 1250 * inverse));
    const scatter = energy * ((betaR * rayleighPhase + betaM * miePhase) / (betaR + betaM));
    let light = Math.pow(scatter * (1 - extinction), 1.5);
    light *= 1 + (Math.pow(scatter * extinction, 0.5) - 1) * dusk;
    const colour = (light + 0.1 * extinction) * 0.04 + [0, 0.0003, 0.00075][c];
    out[c] = Math.pow(colour, 1 / 2.4);
  }
  const luma = out[0] * 0.2126 + out[1] * 0.7152 + out[2] * 0.0722;
  for (let c = 0; c < 3; c++) out[c] = Math.max(0, luma + (out[c] - luma) * SKY_SATURATION);
  return out;
}

const CLOUD_VERTEX = /* glsl */`
  varying vec3 vDir;
  void main() {
    vDir = position;
    vec4 clip = projectionMatrix * vec4(mat3(viewMatrix) * position, 1.0);
    gl_Position = clip.xyww;
  }`;

const CLOUD_FRAGMENT = /* glsl */`
  uniform sampler2D noiseMap;
  uniform float time;
  uniform float coverage;
  uniform float night;
  uniform vec3 lightDir;
  uniform vec3 lightColor;
  uniform vec3 shadeColor;
  uniform vec3 hazeColor;
  uniform vec3 nightZenith;
  uniform vec3 nightHorizon;
  varying vec3 vDir;

  float field(vec2 p) {
    float n = texture2D(noiseMap, p).r * 0.58;
    n += texture2D(noiseMap, p * 3.1 + vec2(0.37, 0.11)).g * 0.27;
    n += texture2D(noiseMap, p * 8.3 + vec2(0.71, 0.53)).b * 0.15;
    return n;
  }

  float star(vec3 d) {
    vec3 p = d * 190.0;
    vec3 cell = floor(p);
    float h = fract(sin(dot(cell, vec3(12.9898, 78.233, 37.719))) * 43758.5453);
    vec3 centre = cell + 0.5 + (vec3(fract(h * 17.3), fract(h * 41.7), fract(h * 73.1)) - 0.5) * 0.6;
    float point = 1.0 - smoothstep(0.0, 0.16, length(p - centre));
    return point * step(0.986, h) * (0.35 + 0.65 * fract(h * 91.7));
  }

  void main() {
    vec3 d = normalize(vDir);
    float up = max(d.y, 0.0);
    float cloud = 0.0;
    vec3 colour = vec3(0.0);
    if (d.y > 0.004) {
      // A flat layer seen from below, bent a little so it does not pile up at the horizon.
      vec2 uv = d.xz / (d.y + 0.16);
      vec2 drift = time * vec2(0.0042, 0.0015);
      float n = field(uv * 0.085 + drift);
      // Billows: push the middle of the range apart so clouds have bodies and clear sky between.
      n = n + (n - 0.5) * 0.35;
      float edge = 0.72 - coverage * 0.34;
      cloud = smoothstep(edge, edge + 0.1, n);
      // Lit from the light's side: thinner cloud towards the light means a bright edge.
      vec2 toLight = lightDir.xz / (lightDir.y + 0.16) - uv;
      toLight = toLight / (length(toLight) + 0.001) * 0.42;
      float n2 = field((uv + toLight) * 0.085 + drift);
      n2 = n2 + (n2 - 0.5) * 0.35;
      float lit = clamp(0.66 + (n - n2) * 3.6, 0.0, 1.0) * (1.0 - 0.28 * smoothstep(edge + 0.1, edge + 0.4, n));
      colour = mix(shadeColor, lightColor, lit);
      float glow = pow(max(dot(d, lightDir), 0.0), 14.0);
      colour += lightColor * glow * (1.0 - cloud * 0.6) * 0.9;
      float lift = smoothstep(0.0, 0.3, d.y);
      colour = mix(hazeColor, colour, 0.25 + 0.75 * lift);
      cloud *= smoothstep(0.004, 0.09, d.y) * 0.95;
    }
    float dark = night;
    vec3 sky = mix(nightHorizon, nightZenith, sqrt(up));
    if (night > 0.02) sky += vec3(0.75, 0.82, 1.0) * star(d) * night * smoothstep(0.02, 0.2, d.y);
    float alpha = 1.0 - (1.0 - cloud) * (1.0 - dark);
    vec3 mixed = (colour * cloud + sky * dark * (1.0 - cloud)) / max(alpha, 0.0001);
    gl_FragColor = vec4(mixed, alpha);
    #include <tonemapping_fragment>
    #include <colorspace_fragment>
  }`;

const FOG_VERTEX = /* glsl */`
  #include <fog_vertex>
  vSkyDir = mvPosition.xyz * mat3(viewMatrix);`;

const FOG_FRAGMENT = /* glsl */`
  #ifdef USE_FOG
    float skyAngle = atan(vSkyDir.z, vSkyDir.x) * 2.546479089470325 + 16.0;
    int skyIndex = int(floor(skyAngle));
    vec3 skyHorizon = mix(skyRing[skyIndex % 16], skyRing[(skyIndex + 1) % 16], skyAngle - float(skyIndex));
    float skyFog = 1.0 - exp(-fogDensity * fogDensity * vFogDepth * vFogDepth);
    gl_FragColor.rgb = mix(gl_FragColor.rgb, skyHorizon, skyFog);
  #endif
  #include <tonemapping_fragment>`;

// Makes a built-in material fade into the sky instead of a single fog colour. The fog is mixed
// before tone mapping, in the same linear light as the sky itself. Call from onBeforeCompile.
export function skyFog(shader, uniforms) {
  shader.uniforms.skyRing = uniforms.skyRing;
  shader.vertexShader = shader.vertexShader
    .replace('#include <fog_pars_vertex>', '#include <fog_pars_vertex>\nvarying vec3 vSkyDir;')
    .replace('#include <fog_vertex>', FOG_VERTEX);
  shader.fragmentShader = shader.fragmentShader
    .replace('#include <fog_pars_fragment>', '#include <fog_pars_fragment>\nvarying vec3 vSkyDir;\nuniform vec3 skyRing[16];')
    .replace('#include <tonemapping_fragment>', FOG_FRAGMENT)
    .replace('#include <fog_fragment>', '');
}

export class Environment {
  constructor(renderer, scene, options = {}) {
    this.renderer = renderer;
    this.scene = scene;
    this.hours = 12;
    this.sunDir = new THREE.Vector3(0, 1, 0);
    this.lightDir = new THREE.Vector3(0, 1, 0);
    this.elevation = 1;
    this.night = 0;
    this.uniforms = { skyRing: { value: Array.from({ length: RING }, () => new THREE.Vector3()) } };
    this.envDirty = true;
    this.envTarget = null;

    this.sky = new Sky();
    this.sky.material.fragmentShader = this.sky.material.fragmentShader.replace(
      'gl_FragColor = vec4( retColor, 1.0 );',
      `float skyLuma = dot( retColor, vec3( 0.2126, 0.7152, 0.0722 ) );
			gl_FragColor = vec4( max( vec3( 0.0 ), skyLuma + ( retColor - skyLuma ) * ${SKY_SATURATION.toFixed(2)} ), 1.0 );`);
    this.sky.scale.setScalar(450000);
    this.sky.renderOrder = 900;          // after everything solid: hidden sky costs nothing
    this.sky.frustumCulled = false;
    Object.entries(ATMOSPHERE).forEach(([name, value]) => { this.sky.material.uniforms[name].value = value; });
    scene.add(this.sky);

    this.cloudMaterial = new THREE.ShaderMaterial({
      name: 'CloudLayer', vertexShader: CLOUD_VERTEX, fragmentShader: CLOUD_FRAGMENT,
      side: THREE.BackSide, depthWrite: false, transparent: true,
      uniforms: {
        noiseMap: { value: noiseTexture() }, time: { value: 0 }, coverage: { value: options.coverage ?? 0.64 },
        night: { value: 0 }, lightDir: { value: new THREE.Vector3(0, 1, 0) },
        lightColor: { value: new THREE.Color() }, shadeColor: { value: new THREE.Color() },
        hazeColor: { value: new THREE.Color() },
        nightZenith: { value: new THREE.Vector3(...NIGHT_ZENITH) }, nightHorizon: { value: new THREE.Vector3(...NIGHT_HORIZON) },
      },
    });
    const dome = new THREE.IcosahedronGeometry(1, 3);
    this.clouds = new THREE.Mesh(dome, this.cloudMaterial);
    this.clouds.frustumCulled = false;
    this.clouds.renderOrder = -100;      // first of the see-through things, right over the sky
    scene.add(this.clouds);

    this.sun = new THREE.DirectionalLight('#ffffff', SUN_INTENSITY);
    this.sun.castShadow = true;
    const size = options.shadowSize || 2048;
    this.sun.shadow.mapSize.set(size, size);
    this.sun.shadow.bias = -0.0002;
    this.sun.shadow.normalBias = 0.05;
    scene.add(this.sun, this.sun.target);
    this.fitCentre = new THREE.Vector3();
    this.fitRadius = 40;

    // What the materials reflect: the same sky and clouds, over a dull ground.
    this.pmrem = new THREE.PMREMGenerator(renderer);
    this.envScene = new THREE.Scene();
    const envSky = new THREE.Mesh(this.sky.geometry, this.sky.material);
    envSky.scale.setScalar(1000);
    const envClouds = new THREE.Mesh(dome, this.cloudMaterial);
    envClouds.renderOrder = 1;
    this.envGround = new THREE.Mesh(new THREE.CircleGeometry(80, 24), new THREE.MeshBasicMaterial({ color: '#000000', fog: false }));
    this.envGround.rotation.x = -Math.PI / 2;
    this.envGround.position.y = -0.6;
    this.envScene.add(envSky, envClouds, this.envGround);

    scene.fog = new THREE.FogExp2('#9fb4c4', options.fogDensity ?? 0.0006);
    this.exposure = DAY_EXPOSURE;
    this.scratch = [0, 0, 0];
    this.setTime(options.hours ?? 12);
  }

  setCoverage(value) {
    this.cloudMaterial.uniforms.coverage.value = value;
    this.envDirty = true;
  }

  setTime(hours) {
    this.hours = ((hours % 24) + 24) % 24;
    const hourAngle = (this.hours - 12) * Math.PI / 12;
    const east = -Math.cos(DECLINATION) * Math.sin(hourAngle);
    const up = Math.sin(LATITUDE) * Math.sin(DECLINATION) + Math.cos(LATITUDE) * Math.cos(DECLINATION) * Math.cos(hourAngle);
    const north = Math.cos(LATITUDE) * Math.sin(DECLINATION) - Math.sin(LATITUDE) * Math.cos(DECLINATION) * Math.cos(hourAngle);
    // Viewer space: east is +x, north (game +z) is -z.
    this.sunDir.set(east, up, -north).normalize();
    this.elevation = Math.asin(this.sunDir.y);
    const degrees = this.elevation * 180 / Math.PI;
    this.night = smooth(-1.5, -9, degrees);
    this.sky.material.uniforms.sunPosition.value.copy(this.sunDir);

    // The sun through the air: redder and weaker the lower it stands.
    const mass = 1 / (Math.max(this.sunDir.y, 0) + 0.15 * Math.pow(Math.max(degrees, 0) + 3.885, -1.253));
    const red = Math.exp(-0.055 * mass);
    const tint = this.sun.color;
    const day = smooth(-2.2, 3.5, degrees);
    const moon = smooth(-4, -10, degrees);
    if (moon > 0 && day <= 0.0001) {
      this.lightDir.set(-this.sunDir.x, Math.max(-this.sunDir.y, 0.3), -this.sunDir.z).normalize();
      tint.setRGB(0.62, 0.74, 1.0);
      this.sun.intensity = MOON_INTENSITY * moon;
    } else {
      this.lightDir.set(this.sunDir.x, Math.max(this.sunDir.y, 0.07), this.sunDir.z).normalize();
      tint.setRGB(1, Math.exp(-0.06 * mass), Math.exp(-0.195 * mass)).lerp(WHITE, 0.14);
      this.sun.intensity = SUN_INTENSITY * Math.pow(red, 0.75) * day;
    }
    // The eye opens up as the light goes: a little at dusk, a lot under the moon.
    this.exposure = DAY_EXPOSURE * (1 + 0.85 * smooth(20, 0, degrees) + 1.5 * this.night);
    this.renderer.toneMappingExposure = this.exposure;

    // The horizon all around, for the fog and for the haze the clouds sink into.
    const ring = this.uniforms.skyRing.value;
    const s = this.scratch;
    let hr = 0, hg = 0, hb = 0;
    for (let i = 0; i < RING; i++) {
      const angle = i * 2 * Math.PI / RING;
      skyRadiance(Math.cos(angle), 0.015, Math.sin(angle), this.sunDir, s);
      for (let c = 0; c < 3; c++) s[c] += (NIGHT_HORIZON[c] - s[c]) * this.night;
      ring[i].set(s[0], s[1], s[2]);
      hr += s[0] / RING; hg += s[1] / RING; hb += s[2] / RING;
    }
    this.scene.fog.color.setRGB(hr, hg, hb);
    const u = this.cloudMaterial.uniforms;
    u.night.value = this.night;
    u.lightDir.value.copy(this.lightDir);
    u.hazeColor.value.setRGB(hr, hg, hb);
    skyRadiance(0, 1, 0, this.sunDir, s);
    const glow = this.night > 0.5 ? 0.3 * moon : 3.4 * Math.pow(red, 0.5) * day;
    u.lightColor.value.copy(tint).multiplyScalar(glow).add(TEMP.setRGB(s[0], s[1], s[2]).multiplyScalar(0.35));
    u.shadeColor.value.setRGB(s[0], s[1], s[2]).multiplyScalar(0.62).add(TEMP.setRGB(hr, hg, hb).multiplyScalar(0.3));
    if (this.night > 0) u.shadeColor.value.lerp(TEMP.setRGB(...NIGHT_HORIZON).multiplyScalar(1.6), this.night);
    // The ground the materials see below the horizon: earth lit by that sky.
    const lift = 0.1 + 0.2 * Math.max(this.sunDir.y, 0);
    this.envGround.material.color.setRGB(hr * lift * 1.25, hg * lift * 1.1, hb * lift * 0.8);
    this.place();
    this.envDirty = true;
  }

  // Aim the light and its shadow box at a sphere around the base.
  fit(centre, radius) {
    this.fitCentre.copy(centre);
    this.fitRadius = radius;
    this.place();
  }

  place() {
    const radius = this.fitRadius;
    const reach = radius * 2 + 60;
    this.sun.position.copy(this.fitCentre).addScaledVector(this.lightDir, reach);
    this.sun.target.position.copy(this.fitCentre);
    this.sun.target.updateMatrixWorld();
    const camera = this.sun.shadow.camera;
    camera.left = camera.bottom = -radius;
    camera.right = camera.top = radius;
    camera.near = reach - radius * 1.6;
    camera.far = reach + radius * 1.6;
    camera.updateProjectionMatrix();
    this.renderer.shadowMap.needsUpdate = true;
  }

  // Rebuild the reflection and ambient light from the sky. The slow step: call when idle.
  refresh() {
    if (!this.envDirty) return false;
    this.envDirty = false;
    const target = this.pmrem.fromScene(this.envScene, 0, 0.1, 200);
    this.scene.environment = target.texture;
    if (this.envTarget) this.envTarget.dispose();
    this.envTarget = target;
    return true;
  }

  tick(seconds) {
    this.cloudMaterial.uniforms.time.value = seconds;
  }

  dispose() {
    if (this.envTarget) this.envTarget.dispose();
    this.pmrem.dispose();
    this.cloudMaterial.dispose();
    this.sky.material.dispose();
    this.sun.shadow.dispose();
  }
}

