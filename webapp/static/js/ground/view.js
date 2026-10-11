// GroundView — ground-level game renderer (Three.js, MIT): real terrain heights with PBR texture
// splatting, sky/sun/HDRI lighting, soft shadows, vegetation, weather, bloom and colour grading,
// articulated vehicles, particles. Same interface as the Cesium BattleScene so the battle page,
// HUD and drive controller work with either.
import * as THREE from 'three';
import { Sky } from 'three/addons/objects/Sky.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';
import { RGBELoader } from 'three/addons/loaders/RGBELoader.js';
import { MapControls } from 'three/addons/controls/MapControls.js';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { ShaderPass } from 'three/addons/postprocessing/ShaderPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import { SMAAPass } from 'three/addons/postprocessing/SMAAPass.js';
import { GroundTerrain, loadTerrainData } from './terrain.js';
import { GroundFx, toThree } from './fx.js';
import { Vegetation } from './veg.js';
import { audio } from './audio.js';
import { loadSoldierKit, makeSoldier } from './soldier.js';
import { Loot, Village, makeVehicle } from './village.js';

const PROCEDURAL = new Set(['car', 'bike', 'jeep']);

const DEG = Math.PI / 180;
const AIR = new Set(['GUNSHIP', 'JET', 'UAV']);
const lerp = (a, b, t) => a + (b - a) * t;
const adiff = (a, b) => { let d = (b - a) % 360; if (d > 180) d -= 360; if (d <= -180) d += 360; return d; };
const lerpA = (a, b, t) => (a + adiff(a, b) * t + 360) % 360;

export const CAM_VIEWS = [
  { id: 'chase', label: 'CHASE' }, { id: 'chase_far', label: 'CHASE FAR' }, { id: 'hull', label: 'HULL CHASE' },
  { id: 'driver', label: "DRIVER'S VIEW" }, { id: 'commander', label: "COMMANDER'S HATCH" },
  { id: 'orbit', label: 'CINEMATIC ORBIT' }, { id: 'top', label: 'TACTICAL TOP-DOWN' },
];
const FOG = { CLEAR: 0.00006, HAZE: 0.00014, RAIN: 0.00028, STORM: 0.00045, SNOW: 0.0003, BLIZZARD: 0.0011, DUST: 0.0008, FOG: 0.0014 };
const GROUND_DUST = { DESERT: [0.85, 0.74, 0.55], MONSOON: [0.45, 0.38, 0.3], MOUNTAIN: [0.55, 0.5, 0.45], ICE: [0.95, 0.97, 1], MARSH: [0.85, 0.83, 0.78] };

const GRADE = {
  uniforms: { tDiffuse: { value: null }, uMode: { value: 0 }, uGain: { value: 1 }, uFlash: { value: 0 }, uHurt: { value: 0 }, uTime: { value: 0 }, uSat: { value: 1.08 }, uVig: { value: 0.32 }, uTint: { value: new THREE.Vector3(1, 1, 1) } },
  vertexShader: 'varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix*modelViewMatrix*vec4(position,1.0); }',
  fragmentShader: `uniform sampler2D tDiffuse; uniform int uMode; uniform float uTime, uSat, uVig, uGain, uFlash, uHurt; uniform vec3 uTint; varying vec2 vUv;
    float rnd(vec2 p){ return fract(sin(dot(p, vec2(12.9898,78.233)) + uTime*7.0)*43758.5453); }
    void main(){
      vec3 c = texture2D(tDiffuse, vUv).rgb;
      float l = dot(c, vec3(0.299,0.587,0.114));
      if (uMode == 1) { // night vision
        float v = pow(clamp(l*uGain, 0.0, 1.0), 0.85) + (rnd(vUv)-0.5)*0.12;
        c = vec3(0.15,1.0,0.35)*v*(0.94+0.06*sin(vUv.y*900.0));
      } else if (uMode == 2) { // thermal white-hot: hot (emissive) objects saturate
        float hot = smoothstep(0.85, 0.98, max(c.r, max(c.g, c.b)));
        float v = clamp(0.12 + l*0.38 + hot*0.8, 0.0, 1.0);
        c = vec3(v);
      } else {
        c = mix(vec3(l), c, uSat) * uTint;
      }
      vec2 d = vUv - 0.5; c *= 1.0 - uVig*dot(d,d)*1.8;
      c += vec3(1.0, 0.85, 0.6) * uFlash;                       // muzzle blast flash
      c = mix(c, vec3(0.6, 0.0, 0.0), uHurt * smoothstep(0.15, 0.75, length(d)));  // damage vignette
      gl_FragColor = vec4(c, 1.0);
    }`,
};

function barTexture(side, hp, morale, flags, name) {
  const c = document.createElement('canvas');
  c.width = 256; c.height = 64;
  const g = c.getContext('2d');
  const col = side === 'BLUE' ? '#36b8ff' : '#ff4b3e';
  g.font = '600 26px Rajdhani, sans-serif'; g.textAlign = 'center';
  g.lineWidth = 5; g.strokeStyle = 'rgba(0,0,0,.85)'; g.strokeText(name, 128, 24); g.fillStyle = col; g.fillText(name, 128, 24);
  g.fillStyle = 'rgba(0,0,0,.65)'; g.fillRect(48, 32, 160, 14);
  g.fillStyle = hp > 60 ? '#4be38a' : hp > 30 ? '#ffb547' : '#ff4b3e'; g.fillRect(50, 34, 156 * hp / 100, 6);
  g.fillStyle = '#9db7ff'; g.fillRect(50, 42, 156 * morale / 100, 3);
  if (flags) { g.font = 'bold 18px Rajdhani'; g.fillStyle = '#ff4b3e'; g.strokeText(flags, 128, 62); g.fillText(flags, 128, 62); }
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

// Disruptive camouflage + paint wear + dust, computed in object space (no UVs needed).
const CAMO = {
  BLUE: [[0.27, 0.31, 0.17], [0.36, 0.3, 0.19], [0.12, 0.13, 0.1]], // Indian Army green / brown / black
  RED: [[0.55, 0.47, 0.33], [0.42, 0.36, 0.26], [0.3, 0.27, 0.22]], // desert tan scheme
};
const _armorTex = {};
function armorTex(name) {
  if (!_armorTex[name]) { const t = new THREE.TextureLoader().load(`/static/textures/${name}`); t.wrapS = t.wrapT = THREE.RepeatWrapping; t.anisotropy = 4; _armorTex[name] = t; }
  return _armorTex[name];
}
function camo(mat, side, biome) {
  const c = CAMO[side] || CAMO.BLUE;
  const dust = { DESERT: [0.75, 0.63, 0.46], ICE: [0.9, 0.92, 0.95], MARSH: [0.8, 0.78, 0.72] }[biome] || [0.5, 0.45, 0.38];
  mat.color.setRGB(1, 1, 1);
  mat.onBeforeCompile = (sh) => {
    sh.uniforms.uC0 = { value: new THREE.Vector3(...c[0]) }; sh.uniforms.uC1 = { value: new THREE.Vector3(...c[1]) };
    sh.uniforms.uC2 = { value: new THREE.Vector3(...c[2]) }; sh.uniforms.uDust = { value: new THREE.Vector3(...dust) };
    sh.uniforms.uArmN = { value: armorTex('armor_nor.jpg') }; sh.uniforms.uArmA = { value: armorTex('armor_arm.jpg') };
    sh.vertexShader = sh.vertexShader.replace('#include <common>', '#include <common>\nvarying vec3 vObj; varying vec3 vObjN; varying vec3 vVX; varying vec3 vVY; varying vec3 vVZ;')
      .replace('#include <begin_vertex>', '#include <begin_vertex>\nvObj = position; vObjN = normal; vVX = normalMatrix*vec3(1,0,0); vVY = normalMatrix*vec3(0,1,0); vVZ = normalMatrix*vec3(0,0,1);');
    sh.fragmentShader = sh.fragmentShader.replace('#include <common>', `#include <common>
      varying vec3 vObj; varying vec3 vObjN; varying vec3 vVX; varying vec3 vVY; varying vec3 vVZ; uniform vec3 uC0, uC1, uC2, uDust; uniform sampler2D uArmN, uArmA;
      vec3 triB(){ vec3 b = pow(abs(vObjN), vec3(4.0)); return b / (b.x+b.y+b.z); }
      vec4 triS(sampler2D t, float sc){ vec3 b = triB(); return texture2D(t, vObj.zy*sc)*b.x + texture2D(t, vObj.xz*sc)*b.y + texture2D(t, vObj.xy*sc)*b.z; }
      float chip;
      float h3(vec3 p){ p = fract(p*0.3183099+0.1); p *= 17.0; return fract(p.x*p.y*p.z*(p.x+p.y+p.z)); }
      float n3(vec3 x){ vec3 i=floor(x), f=fract(x); f=f*f*(3.0-2.0*f);
        return mix(mix(mix(h3(i),h3(i+vec3(1,0,0)),f.x), mix(h3(i+vec3(0,1,0)),h3(i+vec3(1,1,0)),f.x),f.y),
                   mix(mix(h3(i+vec3(0,0,1)),h3(i+vec3(1,0,1)),f.x), mix(h3(i+vec3(0,1,1)),h3(i+vec3(1,1,1)),f.x),f.y), f.z); }`)
      .replace('#include <map_fragment>', `
        float a = n3(vObj*0.55) * 0.65 + n3(vObj*1.3) * 0.35;
        float b = n3(vObj*0.8 + 7.3);
        vec3 paint = mix(uC0, uC1, smoothstep(0.48, 0.52, a));
        paint = mix(paint, uC2, smoothstep(0.62, 0.66, b));
        float grime = n3(vObj*9.0) * 0.12 + n3(vObj*31.0) * 0.06;
        paint *= 0.9 + grime;
        float low = smoothstep(1.2, 0.0, vObj.y);                      // dust on lower hull
        float up = clamp(vObjN.y, 0.0, 1.0) * 0.12;                    // dust settles on top faces
        paint = mix(paint, uDust, clamp(low*0.4 + up*n3(vObj*3.0), 0.0, 0.5));
        // paint chipped down to bare steel in small patches
        chip = smoothstep(0.78, 0.84, n3(vObj*7.0) * 0.7 + n3(vObj*23.0) * 0.3);
        paint = mix(paint, vec3(0.42, 0.41, 0.4), chip);
        diffuseColor.rgb *= paint;`)
      .replace('#include <roughnessmap_fragment>', `float roughnessFactor = mix(0.55 + triS(uArmA, 0.4).g * 0.4, 0.35, chip);`)
      .replace('#include <metalnessmap_fragment>', `float metalnessFactor = mix(0.05, 0.7, chip);`)
      .replace('#include <normal_fragment_maps>', `
        // welded armour-plate detail, triplanar in object space (models carry no UVs)
        { vec3 b = triB();
          vec3 tx = texture2D(uArmN, vObj.zy*0.4).xyz*2.0-1.0, ty = texture2D(uArmN, vObj.xz*0.4).xyz*2.0-1.0, tz = texture2D(uArmN, vObj.xy*0.4).xyz*2.0-1.0;
          vec3 pert = vec3(0.0, tx.y, tx.x)*b.x + vec3(ty.x, 0.0, ty.y)*b.y + vec3(tz.x, tz.y, 0.0)*b.z;
          normal = normalize(normal + (pert.x*vVX + pert.y*vVY + pert.z*vVZ) * 0.55); }`);
  };
  mat.needsUpdate = true;
}

let _blob = null;
function blobTexture() {
  if (_blob) return _blob;
  const c = document.createElement('canvas'); c.width = c.height = 128;
  const g = c.getContext('2d');
  const gr = g.createRadialGradient(64, 64, 8, 64, 64, 64);
  gr.addColorStop(0, 'rgba(0,0,0,0.75)'); gr.addColorStop(0.6, 'rgba(0,0,0,0.35)'); gr.addColorStop(1, 'rgba(0,0,0,0)');
  g.fillStyle = gr; g.fillRect(0, 0, 128, 128);
  _blob = new THREE.CanvasTexture(c);
  return _blob;
}

window.__THREE = THREE;
export class GroundView {
  static async create(container, session, opts = {}) {
    const data = await loadTerrainData(session.scenario.id, session.terrain?.meta?.seed);
    const v = new GroundView(container, session, data, opts);
    await v.init();
    return v;
  }

  constructor(container, session, data, { side = null, onSelect = null, quality = null } = {}) {
    this.container = container;
    this.session = session;
    this.cat = session.catalogue;
    this.data = data;
    this.mySide = side;
    this.onSelect = onSelect;
    this.quality = quality ?? (/(Android|iPhone|iPad)/.test(navigator.userAgent) ? 0.5 : 1);
    this.units = new Map();
    this.projectiles = new Map();
    this.cur = null; this.prev = null; this.tCur = 0;
    this.interval = (session.scenario?.cadence_ms || 250) / 1000;
    this.camMode = 'free'; this.camTarget = null; this.viewIdx = 0; this.rangeMul = 1;
    this.aim = { yaw: 0, pitch: 0 }; this.zoom = 1; this.sensor = 'DAY'; this.showLabels = true;
    this.followPitch = -14; this.followHeadingOffset = 0;
    this.enabled = true;
    this.biome = data.meta.biome;
    this.waterLevel = data.meta.water_level;

    const r = (this.renderer = new THREE.WebGLRenderer({ antialias: false, powerPreference: 'high-performance', logarithmicDepthBuffer: true }));
    r.setPixelRatio(Math.min(window.devicePixelRatio || 1, this.quality >= 1 ? 1.5 : this.quality >= 0.75 ? 1 : 0.75));
    r.setSize(container.clientWidth, container.clientHeight);
    r.toneMapping = THREE.ACESFilmicToneMapping;
    r.outputColorSpace = THREE.SRGBColorSpace;
    r.shadowMap.enabled = true;
    r.shadowMap.type = THREE.PCFSoftShadowMap;
    container.appendChild(r.domElement);
    r.domElement.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;outline:none';
    r.domElement.tabIndex = 0;
    this.canvas = r.domElement;

    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(60, container.clientWidth / container.clientHeight, 0.5, 90000);
    this.clock = new THREE.Clock();
  }

  async init() {
    const sc = this.session.scenario;
    const vil = this.data.meta.village;
    this.terrain = new GroundTerrain(this.renderer, this.scene, this.data, vil
      ? { patchSize: 1400, patchRes: this.quality < 0.6 ? 4 : this.quality < 0.9 ? 3 : 2.5 }
      : { patchSize: this.quality < 0.6 ? 2000 : this.quality < 0.9 ? 2600 : 3200, patchRes: this.quality < 0.6 ? 8 : this.quality < 0.9 ? 6 : 5 });
    if (vil) {
      this.village = new Village(this.scene, vil, this.terrain);
    }
    this.terrain.onRecenter = (cx, cy) => this.veg && this.veg.scatter(cx, cy);
    this.fx = new GroundFx(this.scene, this.quality);
    this.loader = new GLTFLoader().setMeshoptDecoder(MeshoptDecoder);
    if (this.village) this.charTemplate().then((kit) => { this.loot = new Loot(this.scene, kit, this.cat); if (this.pendingItems) this.loot.sync(this.pendingItems); }).catch((e) => console.warn('loot', e));
    this.templates = new Map();
    this.setupLights();
    this.setupSky();
    this.setupPost();
    this.setWeather(sc.weather || 'CLEAR');
    audio.setWeather(sc.weather || 'CLEAR');
    this.freeLook = { yaw: 0, pitch: 0 };
    this.setHour(hourOf(sc.time_of_day));
    this.controls = new MapControls(this.camera, this.canvas);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.controls.maxPolarAngle = 88 * DEG;
    this.controls.minDistance = 8;
    this.controls.maxDistance = 30000;
    this.controls.screenSpacePanning = false;
    this.controls.mouseButtons = { LEFT: THREE.MOUSE.PAN, MIDDLE: THREE.MOUSE.DOLLY, RIGHT: THREE.MOUSE.ROTATE };
    const z = sc.origin ? 0 : 0;
    this.home();
    this.veg = new Vegetation(this.scene, this.terrain, this.data, this.quality);
    if (this.village) this.veg.exclude = (x, y) => !!this.village.buildingAt(x, y) || this.village.near(x, y, 2.5).size > 0;
    this.veg.load().then(() => this.terrain.center && this.veg.scatter(...this.terrain.center)).catch((e) => console.warn('vegetation', e));
    this.raycaster = new THREE.Raycaster();
    window.addEventListener('resize', () => this.resize());
    new ResizeObserver(() => this.resize()).observe(this.container);
    this.fps = 60; this._frames = 0; this._fpsT = performance.now();
    this.loop = this.loop.bind(this);
    requestAnimationFrame(this.loop);
  }

  // ── environment ───────────────────────────────────────────────────────────
  setupLights() {
    this.hemi = new THREE.HemisphereLight(0xcfe4ff, 0x6b5a45, 0.6);
    this.scene.add(this.hemi);
    const sun = (this.sun = new THREE.DirectionalLight(0xfff2dd, 3));
    sun.castShadow = true;
    sun.shadow.mapSize.set(this.quality < 0.8 ? 1024 : 2048, this.quality < 0.8 ? 1024 : 2048);
    const sc = sun.shadow.camera;
    sc.left = -180; sc.right = 180; sc.top = 180; sc.bottom = -180; sc.near = 10; sc.far = 3000;
    sun.shadow.bias = -0.0004;
    sun.shadow.normalBias = 0.6;
    this.scene.add(sun, sun.target);
  }
  setupSky() {
    this.sky = new Sky();
    this.sky.scale.setScalar(80000);
    this.scene.add(this.sky);
    const u = this.sky.material.uniforms;
    u.turbidity.value = 1.6; u.rayleigh.value = 2.6; u.mieCoefficient.value = 0.002; u.mieDirectionalG.value = 0.82;
    this.pmrem = new THREE.PMREMGenerator(this.renderer);
    this.hdr = new RGBELoader();
    // stars for night
    const n = 2500, pos = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) {
      const th = Math.random() * 6.283, ph = Math.acos(Math.random() * 0.95);
      pos.set([Math.sin(ph) * Math.cos(th) * 60000, Math.cos(ph) * 60000, Math.sin(ph) * Math.sin(th) * 60000], i * 3);
    }
    const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    this.stars = new THREE.Points(g, new THREE.PointsMaterial({ color: 0xffffff, size: 1.6, sizeAttenuation: false, fog: false, transparent: true, opacity: 0.85 }));
    this.stars.visible = false;
    this.scene.add(this.stars);
  }
  loadEnv(name) {
    if (this.envName === name) return;
    this.envName = name;
    this.hdr.load(`/static/hdri/${name}.hdr`, (t) => {
      const env = this.pmrem.fromEquirectangular(t).texture;
      t.dispose();
      if (this.scene.environment) this.scene.environment.dispose();
      this.scene.environment = env;
    });
  }
  setupPost() {
    const r = this.renderer, w = this.container.clientWidth, h = this.container.clientHeight;
    this.composer = new EffectComposer(r);
    this.composer.addPass(new RenderPass(this.scene, this.camera));
    // HDR threshold: only genuinely hot things (flashes, fire, emissive coils) bloom — not the sky
    this.bloom = new UnrealBloomPass(new THREE.Vector2(w / 2, h / 2), 0.45, 0.5, 4.0);
    this.composer.addPass(this.bloom);
    this.bloom.enabled = this.quality >= 0.75;
    this.composer.addPass(new OutputPass());
    this.grade = new ShaderPass(GRADE);
    this.composer.addPass(this.grade);
    this.smaa = new SMAAPass(w * r.getPixelRatio(), h * r.getPixelRatio());
    if (this.quality >= 0.75) this.composer.addPass(this.smaa);
  }
  setWeather(w) {
    this.weather = w;
    const dens = FOG[w] ?? FOG.CLEAR;
    this.scene.fog = new THREE.FogExp2(0xbfcad4, dens);
    this.rain = /RAIN|STORM/.test(w);
    this.snow = /SNOW|BLIZZARD/.test(w);
    this.dustStorm = w === 'DUST';
    this.makePrecip();
  }
  makePrecip() {
    if (this.precip) { this.scene.remove(this.precip); this.precip.geometry.dispose(); this.precip = null; }
    if (!this.rain && !this.snow) return;
    const n = Math.floor((this.rain ? 9000 : 7000) * this.quality * (this.weather === 'STORM' || this.weather === 'BLIZZARD' ? 1.6 : 1));
    const pos = new Float32Array(n * (this.rain ? 6 : 3));
    this.precipN = n;
    for (let i = 0; i < n; i++) {
      const x = (Math.random() - 0.5) * 140, y = Math.random() * 70, z = (Math.random() - 0.5) * 140;
      if (this.rain) pos.set([x, y, z, x + 0.15, y + 1.1, z], i * 6); else pos.set([x, y, z], i * 3);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(pos, 3).setUsage(THREE.DynamicDrawUsage));
    this.precip = this.rain
      ? new THREE.LineSegments(g, new THREE.LineBasicMaterial({ color: 0xaab8c6, transparent: true, opacity: 0.38, depthWrite: false }))
      : new THREE.Points(g, new THREE.PointsMaterial({ color: 0xffffff, size: 0.12, transparent: true, opacity: 0.9, depthWrite: false }));
    this.precip.frustumCulled = false;
    this.scene.add(this.precip);
  }

  /** Local solar hour → sun, sky, exposure, environment map, fog colour. */
  setHour(hour) {
    this.hour = hour;
    const el = Math.sin(((hour - 6) / 12) * Math.PI); // -1..1
    const elevDeg = el * 68;
    const az = (90 + (hour - 6) * 15) * DEG; // east → south → west
    const night = elevDeg < -4;
    const sunDir = new THREE.Vector3(Math.sin(az) * Math.cos(Math.max(elevDeg, 2) * DEG), Math.sin(Math.max(elevDeg, 2) * DEG), -Math.cos(az) * Math.cos(Math.max(elevDeg, 2) * DEG));
    this.sunDir = night ? new THREE.Vector3(-0.3, 0.75, 0.4).normalize() : sunDir;
    this.sky.material.uniforms.sunPosition.value.copy(sunDir.clone().multiplyScalar(1000));
    const overcast = /RAIN|STORM|SNOW|BLIZZARD|FOG|DUST/.test(this.weather);
    const golden = !night && elevDeg < 18;
    this.sun.color.setHex(night ? 0x8fa6d8 : golden ? 0xffc48a : 0xfff2dd);
    this.sun.intensity = night ? 0.35 : (overcast ? 1.3 : golden ? 2.4 : 3.4);
    this.hemi.intensity = night ? 0.1 : overcast ? 0.7 : 0.3;
    this.hemi.color.setHex(night ? 0x34406a : 0xcfe4ff);
    this.sky.visible = !night;
    this.stars.visible = night && !overcast;
    this.renderer.toneMappingExposure = night ? 0.55 : overcast ? 0.7 : golden ? 0.75 : 0.62;
    this.scene.background = night ? new THREE.Color(0x05070d) : null;
    const fogCol = night ? 0x0b1020 : this.dustStorm ? 0xb39a72 : overcast ? 0x9aa4ad : golden ? 0xe0b58f : (this.biome === 'DESERT' ? 0xd9cdb6 : 0xbfcad4);
    this.scene.fog.color.setHex(fogCol);
    this.scene.environmentIntensity = night ? 0.12 : overcast ? 0.55 : 0.45;
    this.loadEnv(night ? 'night' : overcast ? 'overcast' : golden ? 'evening' : 'day');
    if (this.terrain?.water) this.terrain.water.material.uniforms.sunDirection.value.copy(this.sunDir);
    this.isNight = night;
    const tint = { DESERT: [1.05, 1.0, 0.92], MONSOON: [0.96, 1.0, 0.98], MOUNTAIN: [1.0, 1.0, 0.98], ICE: [0.95, 0.99, 1.05], MARSH: [1.02, 1.0, 0.98] }[this.biome] || [1, 1, 1];
    this.grade.uniforms.uTint.value.set(...tint);
    this.grade.uniforms.uSat.value = overcast ? 0.92 : 1.1;
  }

  setSensor(mode) {
    const was = this.sensor;
    this.sensor = mode;
    this.grade.uniforms.uMode.value = mode === 'NV' ? 1 : mode === 'THERMAL' ? 2 : 0;
    // image-intensifier gain: strong at night, low in daylight (no green blow-out)
    this.grade.uniforms.uGain.value = this.isNight ? 3.4 : this.hour < 7.5 || this.hour > 17 ? 1.8 : 1.05;
    if (mode === 'THERMAL') { this.sky.visible = false; this.stars.visible = false; this.scene.background = new THREE.Color(0x0b0b0b); }
    else if (was === 'THERMAL') this.setHour(this.hour); // restore sky/background
    this.bloom.enabled = mode === 'DAY' && this.quality >= 0.8;
    if (this.scene.fog) this.scene.fog.density = (FOG[this.weather] ?? FOG.CLEAR) * (mode === 'THERMAL' ? 0.35 : 1);
    for (const v of this.units.values()) this.applyLook(v);
  }

  // ── units ─────────────────────────────────────────────────────────────────
  spec(pid) { return this.cat.platforms[pid] || {}; }
  async template(url) {
    if (!this.templates.has(url)) this.templates.set(url, this.loader.loadAsync(url).then((g) => g.scene));
    return this.templates.get(url);
  }

  ingest(frame, opts = {}) {
    const now = performance.now() / 1000;
    if (this.cur && !opts.instant) { const dt = now - this.tCur; if (dt > 0.01 && dt < 5) this.interval = this.interval * 0.8 + dt * 0.2; }
    this.prev = opts.instant || !this.cur ? frame : this.cur;
    this.cur = frame; this.tCur = now;
    const seen = new Set();
    for (const u of frame.units) {
      seen.add(u.id);
      let v = this.units.get(u.id);
      if (!v) v = this.createUnit(u);
      v.a = opts.instant || !v.b ? u : v.b;
      v.b = u;
      this.updateStatus(v, u);
    }
    for (const [id, v] of this.units) if (!seen.has(id)) { this.removeUnit(v); this.units.delete(id); }
    this.syncProjectiles(frame, opts.instant);
    if (frame.items) { if (this.loot) this.loot.sync(frame.items); else this.pendingItems = frame.items; }
    if (!opts.instant && opts.effects !== false) this.playEvents(frame.events || []);
    if (frame.hour != null && this.session.scenario?.time_of_day === 'DYNAMIC') this.setHour(frame.hour);
  }
  reset() {
    for (const v of this.units.values()) this.removeUnit(v);
    this.units.clear();
    for (const p of this.projectiles.values()) this.removeProjectile(p);
    this.projectiles.clear();
    this.prev = this.cur = null;
  }

  createUnit(u) {
    const spec = this.spec(u.p);
    const air = AIR.has(u.c);
    const root = new THREE.Group();
    const v = { id: u.id, side: u.s, cls: u.c, spec, air, root, nodes: {}, a: null, b: null, dead: false, tilt: { p: 0, r: 0 }, wakeT: 0, smokeT: 0, mats: [] };
    const dims = spec.dims || { length: 8, width: 3.5, height: 2.4 };
    const inf = u.c === 'INF';
    // invisible hit box for picking
    const hb = new THREE.Mesh(inf ? new THREE.BoxGeometry(1.1, 2, 1.1) : new THREE.BoxGeometry(dims.width * 1.2, dims.height * 1.3, dims.length * 1.1), new THREE.MeshBasicMaterial({ visible: false }));
    hb.position.y = dims.height * 0.6;
    hb.userData.unitId = u.id;
    root.add(hb);
    v.hitbox = hb;
    const url = `/static/models/${spec.model || 'mbt'}_${u.s === 'BLUE' ? 'blue' : 'red'}.glb`;
    if (inf) this.createSoldier(v, u);
    else if (PROCEDURAL.has(spec.model)) {
      const m = makeVehicle(spec.model, [...u.id].reduce((a, c) => a + c.charCodeAt(0), 0));
      m.traverse((o) => { if (o.isMesh) { o.material = o.material.clone(); v.mats.push(o.material); } if (o.name === 'gun') v.nodes.gun = o; });
      root.add(m);
      v.model = m;
    } else this.template(u.s === 'NEUT' ? url.replace('_red.glb', '_blue.glb') : url).then((tpl) => {
      if (v.removed) return;
      const m = tpl.clone(true);
      m.traverse((o) => {
        if (o.isMesh) {
          o.castShadow = true; o.receiveShadow = true;
          o.material = o.material.clone();
          // matte military paint (generated models are fairly glossy); glowing parts keep their look
          if (o.material.emissiveIntensity === 0 || o.material.emissive.getHex() === 0) { o.material.roughness = Math.max(0.62, o.material.roughness); o.material.envMapIntensity = 0.6; }
          if (/hull/.test(o.material.name)) camo(o.material, u.s, this.biome);
          v.mats.push(o.material);
        }
        if (['turret', 'gun', 'rotor_l', 'rotor_r', 'prop'].includes(o.name)) v.nodes[o.name] = o;
      });
      root.add(m);
      v.model = m;
      this.applyLook(v);
    });
    // name + health/morale bar
    v.bar = new THREE.Sprite(new THREE.SpriteMaterial({ map: barTexture(u.s, 100, 100, '', u.n), depthTest: true, sizeAttenuation: false, transparent: true }));
    v.bar.scale.set(0.11, 0.0275, 1);
    v.bar.renderOrder = 10;
    v.bar.position.y = dims.height + (u.hvt ? 8 : inf ? 0.75 : 3.5);
    root.add(v.bar);
    if (u.hvt) {
      const ring = new THREE.Mesh(new THREE.RingGeometry(14, 16, 48), new THREE.MeshBasicMaterial({ color: u.s === 'BLUE' ? 0x36b8ff : 0xff4b3e, transparent: true, opacity: 0.6, side: THREE.DoubleSide, depthWrite: false }));
      ring.rotation.x = -Math.PI / 2; ring.position.y = 0.4;
      root.add(ring);
    }
    // soft contact shadow so vehicles sit in the ground
    if (!air) {
      const blob = new THREE.Mesh(new THREE.PlaneGeometry(dims.width * 1.9, dims.length * 1.4), new THREE.MeshBasicMaterial({ map: blobTexture(), transparent: true, depthWrite: false, opacity: 0.75 }));
      blob.rotation.x = -Math.PI / 2; blob.position.y = 0.08; blob.renderOrder = 1;
      root.add(blob);
    }
    // selection ring
    const sr = Math.max(dims.length, 1.6);
    v.sel = new THREE.Mesh(new THREE.RingGeometry(sr * 0.62, sr * 0.7, 40), new THREE.MeshBasicMaterial({ color: 0xffd34d, transparent: true, opacity: 0.9, side: THREE.DoubleSide, depthWrite: false }));
    v.sel.rotation.x = -Math.PI / 2; v.sel.position.y = 0.3; v.sel.visible = false;
    root.add(v.sel);
    this.scene.add(root);
    this.units.set(u.id, v);
    return v;
  }
  charTemplate() {
    if (!this._char) this._char = loadSoldierKit(this.loader);
    return this._char;
  }
  createSoldier(v, u) {
    v.soldier = true; v.anim = null; v.lastShot = -9; v.hitT = -9;
    this.charTemplate().then((kit) => {
      if (v.removed) return;
      const s = makeSoldier(kit, u.s);
      v.root.add(s.root);
      v.model = s.root; v.mixer = s.mixer; v.actions = s.actions; v.setWeapon = s.setWeapon;
      s.mats.push(...v.mats);
      v.mats = s.mats; // guns picked up later add their materials here too
      this.soldierAnim(v, 'Idle_Gun', 0);
      this.applyLook(v);
    }).catch((e) => console.warn('soldier model', e));
  }
  soldierAnim(v, name, fade = 0.18, speed = 1) {
    if (!v.actions) return;
    const next = v.actions[name] || v.actions.Idle_Gun;
    next.timeScale = speed;
    if (v.anim === name) return;
    const prev = v.actions[v.anim];
    next.reset().setEffectiveWeight(1).play();
    if (prev && fade > 0) prev.crossFadeTo(next, fade, false);
    else if (prev) prev.stop();
    v.anim = name;
  }
  /** Local movement intent for client-side prediction of the player's own soldier. */
  setLocalMove(m) { this.local = m ? { ...(this.local || {}), ...m } : null; }
  updateSoldier(v, a, b, k, dt, now) {
    const mounted = !!b.mt;
    v.root.visible = !mounted;
    let x = lerp(a.x, b.x, k), y = lerp(a.y, b.y, k), h = lerpA(a.h, b.h, k);
    const L = this.local;
    const own = L && L.id === v.id && this.camTarget === v.id && !v.dead && !mounted;
    let mv = 0, fwd = Math.sign(b.th || 0), strafe = 0;
    if (own) {
      // client-side prediction: integrate the player's input every frame, converge on the server's position
      if (!L.pos || Math.hypot(L.pos[0] - b.x, L.pos[1] - b.y) > 12) L.pos = [b.x, b.y];
      const yaw = this.aim.yaw * DEG;
      let vx = Math.sin(yaw) * L.fwd + Math.cos(yaw) * L.strafe, vy = Math.cos(yaw) * L.fwd - Math.sin(yaw) * L.strafe;
      const n = Math.hypot(vx, vy);
      const sp = n > 0.01 ? (b.sw ? 0.9 : L.crouch ? 1 : L.sprint && L.fwd > 0.3 ? 5.4 : 1.9) : 0;
      if (n > 0.01) { vx /= n; vy /= n; }
      const ox = L.pos[0], oy = L.pos[1];
      L.pos[0] += vx * sp * dt; L.pos[1] += vy * sp * dt;
      const lag = Math.min(0.6, now - this.tCur + 0.08);
      const kc = 1 - Math.exp(-dt * 2.5);
      L.pos[0] += (b.x + vx * sp * lag - L.pos[0]) * kc;
      L.pos[1] += (b.y + vy * sp * lag - L.pos[1]) * kc;
      if (this.village) [L.pos[0], L.pos[1]] = this.village.collide(ox, oy, L.pos[0], L.pos[1]);
      [x, y] = L.pos; h = this.aim.yaw; mv = sp; fwd = L.fwd; strafe = L.strafe;
    } else {
      mv = Math.hypot(b.x - a.x, b.y - a.y) / Math.max(0.05, this.interval);
      if (Math.abs(b.v) < 0.05) mv = Math.min(mv, Math.abs(b.v));
    }
    let z = this.village ? this.village.groundZ(x, y) : this.terrain.height(x, y);
    if (b.sw && this.waterLevel != null) z = this.waterLevel - 1.25;
    v.pos = [x, y, z]; v.heading = h;
    if (v.setWeapon && b.cw) v.setWeapon(this.cat.weapons[b.cw]?.model || 'AK');
    v.root.position.set(x, z, -y);
    v.root.rotation.set(0, Math.PI - h * DEG, 0, 'YXZ');
    v.bar.visible = this.showLabels && !own && !mounted && !v.dead && !(this.village && this.mySide && v.side !== this.mySide);
    if (!v.mixer || mounted) return;
    // animation state machine
    const crouch = b.stn === 'CROUCH';
    const shooting = now - v.lastShot < 0.45;
    let anim = 'Idle_Gun', rate = 1;
    if (v.dead) anim = 'Death';
    else if (now - v.hitT < 0.35) anim = 'HitRecieve';
    else if (mv > 0.25) {
      if (b.sw) { anim = 'Walk'; rate = 0.6; }
      else if (own && Math.abs(strafe) > Math.abs(fwd)) { anim = strafe > 0 ? 'Run_Right' : 'Run_Left'; rate = Math.max(0.55, mv / 4.5); }
      else if (fwd < -0.1) { anim = 'Run_Back'; rate = Math.max(0.5, mv / 4); }
      else if (mv > 3) { anim = shooting ? 'Run_Shoot' : 'Run'; rate = mv / 5.2; }
      else { anim = 'Walk'; rate = Math.max(0.6, mv / 1.9); }
    } else if (shooting) anim = 'Idle_Gun_Shoot';
    else if (crouch || (own && this.camMode === 'scope')) anim = 'Idle_Gun_Pointing';
    this.soldierAnim(v, anim, anim === 'Death' ? 0.1 : 0.2, rate);
    if (v.model) v.model.scale.y += ((crouch && !v.dead ? 0.8 : 1) - v.model.scale.y) * (1 - Math.exp(-dt * 10));
    // distant soldiers animate at a lower rate
    const far = this.camera.position.distanceToSquared(v.root.position) > 350 * 350;
    v.animAcc = (v.animAcc || 0) + dt;
    if (!far || v.animAcc > 0.1) { v.mixer.update(v.animAcc); v.animAcc = 0; }
  }
  removeUnit(v) {
    v.removed = true;
    this.scene.remove(v.root);
    v.bar.material.map?.dispose();
    for (const m of v.mats) m.dispose();
  }
  updateStatus(v, u) {
    const hp = Math.round((u.hp / u.mhp) * 20) * 5, m = Math.round((u.m ?? 100) / 10) * 10;
    const flags = u.st === 'DESTROYED' ? 'DESTROYED' : u.mk ? 'IMMOBILISED' : u.fk ? 'WEAPONS OUT' : u.sw ? '≈ SWIMMING' : '';
    const key = `${hp}|${m}|${flags}`;
    if (v.barKey !== key) { v.barKey = key; v.bar.material.map?.dispose(); v.bar.material.map = barTexture(u.s, hp, m, flags, u.n); v.bar.material.needsUpdate = true; }
    const dead = u.st === 'DESTROYED';
    if (dead !== v.dead) { v.dead = dead; this.applyLook(v); }
  }
  applyLook(v) {
    for (const m of v.mats) {
      if (!m.userData.base) m.userData.base = { color: m.color.clone(), emissive: m.emissive.clone(), ei: m.emissiveIntensity };
      const b = m.userData.base;
      if (v.dead) { m.color.copy(b.color).multiplyScalar(0.12); m.emissive.setHex(0x000000); }
      else if (this.sensor === 'THERMAL') { m.color.copy(b.color); m.emissive.setHex(0xffffff); m.emissiveIntensity = 2.5; }
      else { m.color.copy(b.color); m.emissive.copy(b.emissive); m.emissiveIntensity = b.ei; }
    }
    v.sel.visible = this.selected === v.id;
  }

  syncProjectiles(frame, instant) {
    const seen = new Set();
    for (const p of frame.projectiles || []) {
      seen.add(p.id);
      let v = this.projectiles.get(p.id);
      if (!v) {
        v = { id: p.id, kind: p.k, a: p, b: p, trailT: 0 };
        if (p.k === 'MISSILE') this.template('/static/models/missile.glb').then((t) => { if (v.removed) return; v.obj = t.clone(true); v.obj.scale.setScalar(1.6); this.scene.add(v.obj); });
        this.projectiles.set(p.id, v);
      }
      v.a = instant ? p : v.b; v.b = p;
    }
    for (const [id, v] of this.projectiles) if (!seen.has(id)) { this.removeProjectile(v); this.projectiles.delete(id); }
  }
  removeProjectile(v) { v.removed = true; if (v.obj) this.scene.remove(v.obj); }

  playEvents(events) {
    const dust = GROUND_DUST[this.biome];
    for (const ev of events) {
      if (ev.t === 'FIRE') {
        const sv = this.units.get(ev.shooter);
        let from = ev.from;
        if (sv?.pos) {
          const d = sv.spec.dims || { height: 2.4, length: 8 };
          const yaw = (sv.b?.ty ?? sv.heading) * DEG;
          from = sv.soldier
            ? [sv.pos[0] + Math.sin(yaw) * 0.7 + Math.cos(yaw) * 0.2, sv.pos[1] + Math.cos(yaw) * 0.7 - Math.sin(yaw) * 0.2, sv.pos[2] + (sv.b?.stn === 'CROUCH' ? 1.05 : 1.38)]
            : [sv.pos[0] + Math.sin(yaw) * d.length * 0.55, sv.pos[1] + Math.cos(yaw) * d.length * 0.55, sv.pos[2] + d.height * 0.8];
          if (sv.soldier) sv.lastShot = performance.now() / 1000;
        }
        this.fx.fire(ev, from, sv?.side);
        const mine = ev.shooter === this.drivenId;
        audio.shot(ev.fx, ev.weapon, toThree(from), mine);
        if (mine) this.kick(ev.fx === 'TRACER' && /hmg|ac-|rifle/.test(ev.weapon || '') ? (/rifle/.test(ev.weapon) ? 0.12 : 0.25) : 1);
        if (ev.target && ev.target === this.drivenId) audio.crack();
      } else if (ev.t === 'IMPACT') {
        const at = [ev.at[0], ev.at[1], ev.hit ? ev.at[2] : this.terrain.height(ev.at[0], ev.at[1])];
        this.fx.impact({ ...ev, at }, this.waterLevel != null && at[2] <= this.waterLevel + 0.4, dust);
        const tp = toThree(at);
        if (ev.hit && ev.effect !== 'NO_PEN') audio.explosion(tp, ev.effect === 'DESTROYED' ? 2 : 1);
        else if (ev.effect === 'BLAST') audio.explosion(tp, 1.5);
        else audio.impact(tp, ev.effect);
        if (ev.hit && ev.target === this.drivenId) { this.kick(2.5); audio.hitOwn(); this.hurt = 1; }
        const tv = ev.hit && ev.target ? this.units.get(ev.target) : null;
        if (tv?.soldier) tv.hitT = performance.now() / 1000;
      } else if (ev.t === 'INTERCEPT') { this.fx.intercept(ev.at); audio.explosion(toThree(ev.at), 0.4); }
    }
  }

  // ── selection, picking, projection (interface used by battle.js / hud.js) ─
  ndc(screen) {
    const r = this.canvas.getBoundingClientRect();
    return new THREE.Vector2((screen.x / r.width) * 2 - 1, -(screen.y / r.height) * 2 + 1);
  }
  pickUnit(screen) {
    this.raycaster.setFromCamera(this.ndc(screen), this.camera);
    const boxes = [...this.units.values()].map((v) => v.hitbox);
    const hit = this.raycaster.intersectObjects(boxes, false)[0];
    return hit ? hit.object.userData.unitId : null;
  }
  pickLocal(screen) {
    this.raycaster.setFromCamera(this.ndc(screen), this.camera);
    const ray = this.raycaster.ray;
    return this.terrain.raycast(ray.origin, ray.direction);
  }
  project(p) {
    const v = toThree(p).project(this.camera);
    if (v.z > 1) return null;
    const r = this.canvas.getBoundingClientRect();
    return { x: (v.x + 1) / 2 * r.width, y: (1 - v.y) / 2 * r.height };
  }
  /** World point under the crosshair (scope: camera centre; otherwise along the gun line). */
  aimPoint(scopeOn, unitId) {
    let origin, dir;
    if (scopeOn || this.units.get(unitId)?.soldier) {
      origin = this.camera.position.clone();
      dir = new THREE.Vector3(0, 0, -1).applyQuaternion(this.camera.quaternion);
    } else {
      const v = this.units.get(unitId);
      if (!v?.pos) return null;
      const d = v.spec.dims || { height: 2.4 };
      origin = toThree([v.pos[0], v.pos[1], v.pos[2] + d.height]);
      const y = this.aim.yaw * DEG, p = this.aim.pitch * DEG;
      dir = new THREE.Vector3(Math.sin(y) * Math.cos(p), Math.sin(p), -Math.cos(y) * Math.cos(p));
    }
    this.raycaster.set(origin, dir);
    const own = this.units.get(unitId)?.hitbox;
    const hit = this.raycaster.intersectObjects([...this.units.values()].map((v) => v.hitbox).filter((h) => h !== own), false)[0];
    let tHit = this.terrain.raycast(origin, dir, 10000);
    if (this.village) {
      const far = origin.clone().addScaledVector(dir, 1200);
      const t = this.village.rayT(origin.x, -origin.z, origin.y, far.x, -far.z, far.y);
      if (t !== null) {
        const p = origin.clone().addScaledVector(dir, 1200 * t);
        if (!tHit || origin.distanceTo(p) < origin.distanceTo(toThree(tHit))) tHit = [p.x, -p.z, p.y];
      }
    }
    if (hit) {
      const tp = [hit.point.x, -hit.point.z, hit.point.y];
      if (!tHit || hit.distance < origin.distanceTo(toThree(tHit))) return tp;
    }
    return tHit;
  }
  select(id) {
    const old = this.selected;
    this.selected = id;
    for (const k of [old, id]) { const v = this.units.get(k); if (v) this.applyLook(v); }
    this.onSelect && this.onSelect(id);
  }
  view(id) { return this.cur?.units.find((u) => u.id === id) || null; }
  rendered(id) { return this.units.get(id)?.pos || null; }
  setInputs(on) { this.controls.enabled = on; }

  // ── cameras ───────────────────────────────────────────────────────────────
  home() {
    // low over the own zone, looking toward the enemy with the horizon in view
    const zones = this.session.zones || [];
    const cen = (z) => [z.polygon.reduce((s, p) => s + p[0], 0) / z.polygon.length, z.polygon.reduce((s, p) => s + p[1], 0) / z.polygon.length];
    const own = zones.find((z) => z.side === (this.mySide || 'BLUE')) || zones[0];
    const foe = zones.find((z) => z !== own);
    const [cx, cy] = own ? cen(own) : [0, 0];
    const [ex, ey] = foe ? cen(foe) : [cx, cy + 5000];
    const d = Math.hypot(ex - cx, ey - cy) || 1, ux = (ex - cx) / d, uy = (ey - cy) / d;
    const tx = cx + ux * 900, ty = cy + uy * 900;
    const th = this.terrain.height(tx, ty);
    this.controls.target.set(tx, th, -ty);
    const px = cx - ux * 350, py = cy - uy * 350;
    this.camera.position.set(px, Math.max(this.terrain.height(px, py), th) + 180, -py);
    this.controls.update();
  }
  flyTo(x, y, dist = 600) {
    const h = this.terrain.height(x, y);
    this.freeCam();
    this.controls.target.set(x, h, -y);
    const cx = x, cy = y - dist * 0.8;
    this.camera.position.set(cx, Math.max(h + dist * 0.6, this.terrain.height(cx, cy) + 25), -cy);
  }
  freeCam() {
    if (this.hiddenOwn?.model) this.hiddenOwn.model.visible = true;
    this.hiddenOwn = null;
    if (this.camMode !== 'free' && this.camTarget) {
      const v = this.units.get(this.camTarget);
      if (v?.pos) this.controls.target.copy(toThree(v.pos));
    }
    this.camMode = 'free';
    this.camTarget = null;
    this.camera.fov = 60; this.camera.updateProjectionMatrix();
    this.controls.enabled = true;
    this.setSensor('DAY');
  }
  follow(id) { this.camMode = 'follow'; this.camTarget = id; this.camSmooth = null; this.controls.enabled = false; }
  drive(id) { this.camMode = 'drive'; this.camTarget = id; this.camSmooth = null; this.controls.enabled = false; }
  scope(id) { this.camMode = 'scope'; this.camTarget = id; }
  /** Recoil / impact shake. */
  kick(s) { this.shake = Math.min(3, (this.shake || 0) + s); this.flash = Math.max(this.flash || 0, this.camMode === 'scope' ? 0.35 * Math.min(1, s) : 0); }
  cycleView(dir = 1) { this.viewIdx = (this.viewIdx + dir + CAM_VIEWS.length) % CAM_VIEWS.length; this.camSmooth = null; return CAM_VIEWS[this.viewIdx]; }
  get viewLabel() { return CAM_VIEWS[this.viewIdx].label; }

  updateCamera(dt, now) {
    this.placeCamera(dt, now);
    const cam = this.camera;
    // shake / recoil, damage flash, free-look decay
    if (this.shake > 0.01) {
      const a = this.shake * 0.006;
      cam.rotateX((Math.random() - 0.4) * a); cam.rotateY((Math.random() - 0.5) * a);
      this.shake *= Math.exp(-dt * 7);
    }
    this.flash = (this.flash || 0) * Math.exp(-dt * 14);
    this.hurt = (this.hurt || 0) * Math.exp(-dt * 1.6);
    this.grade.uniforms.uFlash.value = this.flash;
    this.grade.uniforms.uHurt.value = Math.max(this.hurt, this.lowHp ? 0.25 + Math.sin(now * 4) * 0.08 : 0);
    if (!this.freeLookActive && this.freeLook) { this.freeLook.yaw *= Math.exp(-dt * 6); this.freeLook.pitch *= Math.exp(-dt * 6); }
    audio.setListener(cam.position, new THREE.Vector3(0, 0, -1).applyQuaternion(cam.quaternion));
  }

  placeCamera(dt, now) {
    const cam = this.camera;
    if (this.camMode === 'free' || !this.camTarget) {
      this.controls.update();
      // never let the free camera dip into a hillside
      const gh = this.terrain.height(cam.position.x, -cam.position.z) + 4;
      if (cam.position.y < gh) cam.position.y = gh;
      return;
    }
    const v = this.units.get(this.camTarget);
    if (!v?.pos) return;
    if (v.soldier && v.root.visible) return this.soldierCamera(v, dt, now);
    const [x, y, z] = v.pos;
    const d = v.spec.dims || { length: 8, width: 3.5, height: 2.4 };
    const eyeH = d.height * 0.95 + (v.cls === 'RECON' ? 1.5 : 0);
    const vw = this.camMode === 'scope' ? 'scope' : CAM_VIEWS[this.viewIdx].id;
    const hull = v.heading;
    const aimYaw = this.camMode === 'drive' ? this.aim.yaw : hull, aimPitch = this.camMode === 'drive' ? this.aim.pitch : 0;
    const k = 1 - Math.exp(-dt * 9);
    const want = vw === 'hull' || vw === 'driver' || vw === 'top' ? hull : vw === 'orbit' ? (now * 12) % 360 : aimYaw;
    if (!this.camSmooth) this.camSmooth = { yaw: want, pitch: aimPitch };
    this.camSmooth.yaw = (this.camSmooth.yaw + adiff(this.camSmooth.yaw, want) * (vw === 'orbit' ? 1 : k) + 360) % 360;
    this.camSmooth.pitch += (aimPitch - this.camSmooth.pitch) * k;
    const fl = this.freeLook || { yaw: 0, pitch: 0 };
    const yaw = (this.camSmooth.yaw + (vw === 'scope' ? 0 : fl.yaw) + 360) % 360, pitch = this.camSmooth.pitch + (vw === 'scope' ? 0 : fl.pitch);
    const hideOwn = vw === 'scope' || vw === 'driver';
    if (v.model && v.model.visible === hideOwn) v.model.visible = !hideOwn;
    v.bar.visible = !hideOwn && this.camMode !== 'drive';
    if (this.hiddenOwn && this.hiddenOwn !== v && this.hiddenOwn.model) this.hiddenOwn.model.visible = true;
    this.hiddenOwn = hideOwn ? v : null;
    const dirOf = (h, p) => new THREE.Vector3(Math.sin(h * DEG) * Math.cos(p * DEG), Math.sin(p * DEG), -Math.cos(h * DEG) * Math.cos(p * DEG));
    if (vw === 'scope' || vw === 'driver' || vw === 'commander') {
      let px = x, py = y, pz = z + eyeH + 0.4, hd = this.aim.yaw, pt = this.aim.pitch;
      if (vw === 'driver') { const f = d.length * 0.38; px += Math.sin(hull * DEG) * f; py += Math.cos(hull * DEG) * f; pz = z + d.height * 0.7; hd = yaw; pt = (v.b?.pi || 0) - 2; }
      else if (vw === 'commander') { pz = z + d.height + 1.1; hd = yaw; pt = pitch - 4; }
      const pos = toThree([px, py, pz]);
      cam.position.copy(pos);
      cam.lookAt(pos.clone().add(dirOf(hd, pt)));
      const fov = vw === 'scope' ? 55 / this.zoom : vw === 'driver' ? 75 : 65;
      if (cam.fov !== fov) { cam.fov = fov; cam.updateProjectionMatrix(); }
      return;
    }
    if (cam.fov !== 60) { cam.fov = 60; cam.updateProjectionMatrix(); }
    const center = toThree([x, y, z + eyeH]);
    const base = Math.max(v.cls === 'CAR' ? 5.5 : 14, d.length * 2.6) * this.rangeMul;
    let range = base, pt = vw === 'chase' ? pitch - 12 : -14;
    if (this.camMode === 'follow' && vw === 'chase') pt = this.followPitch;
    if (vw === 'chase_far') { range = base * 3.2; pt = -22; }
    if (vw === 'orbit') { range = base * 1.6; pt = -12; }
    if (vw === 'top') { range = Math.max(180, base * 9); pt = -89; }
    const pos = center.clone().sub(dirOf(yaw, pt).multiplyScalar(range));
    // keep the camera above the ground
    const gp = [pos.x, -pos.z];
    const gh = this.terrain.height(gp[0], gp[1]) + 1.5;
    if (pos.y < gh) pos.y = gh;
    cam.position.copy(pos);
    cam.lookAt(center);
  }

  /** PUBG-style over-the-shoulder third person, aim-down-sights and first person (V) for soldiers. */
  soldierCamera(v, dt, now) {
    const cam = this.camera;
    const [x, y, z] = v.pos;
    const driving = this.camMode === 'drive' || this.camMode === 'scope';
    const ads = this.camMode === 'scope';
    const fp = (this.infFP && driving) || ads; // aiming looks down the sights
    const fl = ads ? { yaw: 0, pitch: 0 } : (this.freeLook || { yaw: 0, pitch: 0 });
    const yaw = (driving ? this.aim.yaw : v.heading) + fl.yaw;
    const pitch = (driving ? this.aim.pitch : -12) + fl.pitch;
    const crouch = v.b?.stn === 'CROUCH';
    const eye = v.dead ? 0.5 : v.b?.sw ? 0.35 : crouch ? 1.2 : 1.66;
    const f = new THREE.Vector3(Math.sin(yaw * DEG) * Math.cos(pitch * DEG), Math.sin(pitch * DEG), -Math.cos(yaw * DEG) * Math.cos(pitch * DEG));
    const right = new THREE.Vector3(Math.cos(yaw * DEG), 0, Math.sin(yaw * DEG));
    const head = toThree([x, y, z + eye]);
    let pos;
    if (fp) pos = head.clone().addScaledVector(f, 0.28);
    else {
      this.adsBlend = (this.adsBlend || 0) + ((ads ? 1 : 0) - (this.adsBlend || 0)) * (1 - Math.exp(-dt * 14));
      const ab = this.adsBlend;
      const back = lerp(3.4 * Math.min(2.5, this.rangeMul), 1.35, ab), side = lerp(0.62, 0.48, ab), up = lerp(0.32, 0.08, ab);
      pos = head.clone().addScaledVector(f, -back).addScaledVector(right, side);
      pos.y += up;
      // pull in when a wall or a hillside is between the head and the camera
      if (this.village) {
        const t = this.village.rayT(head.x, -head.z, head.y, pos.x, -pos.z, pos.y);
        if (t !== null) pos = head.clone().lerp(pos, Math.max(0.05, t - 0.12));
      }
      for (let t = 0.25; t <= 1.001; t += 0.25) {
        const p = head.clone().lerp(pos, t);
        if (p.y < this.terrain.height(p.x, -p.z) + 0.35) { pos = head.clone().lerp(pos, Math.max(0.1, t - 0.25)); break; }
      }
    }
    const gh = this.terrain.height(pos.x, -pos.z) + 0.3;
    if (pos.y < gh) pos.y = gh;
    cam.position.copy(pos);
    cam.lookAt(pos.clone().add(f));
    if (v.model) v.model.visible = !fp;
    if (this.hiddenOwn && this.hiddenOwn !== v && this.hiddenOwn.model) this.hiddenOwn.model.visible = true;
    this.hiddenOwn = fp ? v : null;
    const fov = ads ? 62 / Math.max(1, this.zoom) : fp ? 80 : 72;
    if (Math.abs(cam.fov - fov) > 0.05) { cam.fov += (fov - cam.fov) * Math.min(1, dt * 16); cam.updateProjectionMatrix(); }
  }

  // ── frame loop ────────────────────────────────────────────────────────────
  alpha() { return Math.min(1, (performance.now() / 1000 - this.tCur) / Math.max(0.03, this.interval)); }
  resize() {
    const w = this.container.clientWidth, h = this.container.clientHeight;
    if (!w || !h) return;
    this.camera.aspect = w / h; this.camera.updateProjectionMatrix();
    this.renderer.setSize(w, h);
    this.composer.setSize(w, h);
  }
  setEnabled(on) { this.enabled = on; if (on) { this.resize(); this.clock.getDelta(); } }

  loop() {
    requestAnimationFrame(this.loop);
    if (!this.enabled) return;
    const dt = Math.min(0.1, this.clock.getDelta());
    const now = performance.now() / 1000;
    const k = this.alpha();
    for (const v of this.units.values()) this.updateUnit(v, k, dt, now);
    for (const p of this.projectiles.values()) this.updateProjectile(p, k, now);
    this.updateCamera(dt, now);
    // focus point drives terrain detail, shadows and vegetation
    const focus = this.camMode === 'free' ? this.controls.target : (this.units.get(this.camTarget)?.root.position || this.controls.target);
    const fx = focus.x, fy = -focus.z;
    this.terrain.update(fx, fy);
    if (this.veg) this.veg.grass(this.camera.position.x, -this.camera.position.z, now);
    const s = this.sun;
    s.target.position.set(fx, focus.y, -fy);
    s.position.copy(s.target.position).add(this.sunDir.clone().multiplyScalar(1200));
    if (this.precip) this.updatePrecip(dt);
    this.fx.update(dt);
    if (this.terrain.water) this.terrain.water.material.uniforms.time.value += dt * 0.6;
    this.grade.uniforms.uTime.value = now;
    if (this.village) this.village.update(now);
    this.composer.render();
    this.adapt();
  }

  updateUnit(v, k, dt, now) {
    const a = v.a, b = v.b;
    if (!a || !b) return;
    if (v.soldier) return this.updateSoldier(v, a, b, k, dt, now);
    const x = lerp(a.x, b.x, k), y = lerp(a.y, b.y, k), h = lerpA(a.h, b.h, k);
    let z;
    const d = v.spec.dims || { length: 8, width: 3.5, height: 2.4 };
    if (v.air) z = lerp(a.z, b.z, k);
    else {
      z = this.terrain.height(x, y) + (v.spec.hover ? 0.6 : 0);
      if (this.waterLevel != null && b.sw) z = Math.max(z, this.waterLevel - d.height * 0.45);
    }
    v.pos = [x, y, z];
    v.heading = h;
    v.root.position.set(x, z, -y);
    // suspension: tilt from the rendered surface under the hull, smoothed
    let pitch = 0, roll = 0;
    if (!v.air) {
      const fx = Math.sin(h * DEG), fy = Math.cos(h * DEG), rx = Math.cos(h * DEG), ry = -Math.sin(h * DEG);
      const L = d.length * 0.45, W = d.width * 0.45, hh = this.terrain.height;
      pitch = Math.atan2(hh(x + fx * L, y + fy * L) - hh(x - fx * L, y - fy * L), 2 * L);
      roll = Math.atan2(hh(x - rx * W, y - ry * W) - hh(x + rx * W, y + ry * W), 2 * W);
      if (b.sw) { pitch *= 0.2; roll = Math.sin(now * 1.3 + x) * 0.03; }
    } else pitch = -lerp(a.pi, b.pi, k) * DEG * -1;
    const kk = 1 - Math.exp(-dt * 10);
    v.tilt.p += (pitch - v.tilt.p) * kk; v.tilt.r += (roll - v.tilt.r) * kk;
    v.root.rotation.set(-v.tilt.p + (v.dead ? 0.05 : 0), Math.PI - h * DEG, v.tilt.r + (v.dead ? 0.08 : 0), 'YXZ');
    const n = v.nodes;
    if (n.turret) n.turret.rotation.y = -adiff(h, lerpA(a.ty, b.ty, k)) * DEG;
    if (n.gun) n.gun.rotation.x = -lerp(a.gp || 0, b.gp || 0, k) * DEG;
    if (n.rotor_l) { n.rotor_l.rotation.y += dt * 25; n.rotor_r.rotation.y -= dt * 25; }
    if (n.prop) n.prop.rotation.z += dt * 40;
    v.bar.visible = this.showLabels && v !== this.hiddenOwn && !(this.camMode === 'drive' && v.id === this.camTarget);
    // dust / spray behind moving vehicles, smoke from wrecks
    if (!v.air && !v.dead && Math.abs(b.v) > 2.5 && now - v.wakeT > 0.12) {
      v.wakeT = now;
      const back = [x - Math.sin(h * DEG) * d.length * 0.5, y - Math.cos(h * DEG) * d.length * 0.5, z + 0.5];
      this.fx.wake(back, Math.abs(b.v), b.sw ? 'WATER' : b.gr);
    }
    if (v.dead && now - v.smokeT > 0.4 && this.camMode !== 'scope') { v.smokeT = now; this.fx.wreckSmoke([x, y, z]); }
  }
  updateProjectile(p, k, now) {
    const x = lerp(p.a.x, p.b.x, k), y = lerp(p.a.y, p.b.y, k), z = lerp(p.a.z, p.b.z, k);
    if (p.obj) {
      p.obj.position.set(x, z, -y);
      const dx = p.b.x - p.a.x, dy = p.b.y - p.a.y, dz = p.b.z - p.a.z;
      if (dx || dy || dz) p.obj.lookAt(x + dx, z + dz, -(y + dy));
    }
    if (now - p.trailT > (p.kind === 'MISSILE' ? 0.03 : 0.06)) { p.trailT = now; this.fx.trail([x, y, z], p.kind === 'MISSILE'); }
  }
  updatePrecip(dt) {
    const pos = this.precip.geometry.attributes.position;
    const arr = pos.array, c = this.camera.position;
    const stride = this.rain ? 6 : 3;
    const fall = this.rain ? 26 : 2.2;
    if (!this._pc) this._pc = c.clone();
    const ox = c.x - this._pc.x, oy = c.y - this._pc.y, oz = c.z - this._pc.z;
    this._pc.copy(c);
    const t = performance.now() / 1000;
    for (let i = 0; i < this.precipN; i++) {
      const o = i * stride;
      let y = arr[o + 1] - fall * dt - oy;
      let x = arr[o] - ox, z = arr[o + 2] - oz;
      if (this.snow) { x += Math.sin(t + i) * 0.02; }
      if (y < -20) { y += 90; x = (Math.random() - 0.5) * 140; z = (Math.random() - 0.5) * 140; }
      if (x > 70) x -= 140; if (x < -70) x += 140; if (z > 70) z -= 140; if (z < -70) z += 140;
      arr[o] = x; arr[o + 1] = y; arr[o + 2] = z;
      if (this.rain) { arr[o + 3] = x + 0.15; arr[o + 4] = y + 1.1; arr[o + 5] = z; }
    }
    this.precip.position.copy(c);
    pos.needsUpdate = true;
  }
  adapt() {
    this._frames++;
    const t = performance.now();
    if (t - this._fpsT < 2000) return;
    this.fps = (this._frames * 1000) / (t - this._fpsT);
    this._frames = 0; this._fpsT = t;
    const pr = this.renderer.getPixelRatio();
    if (this.fps < 35 && pr > 0.6) { this.renderer.setPixelRatio(Math.max(0.6, pr - 0.15)); this.resize(); }
    else if (this.fps > 55 && pr < Math.min(window.devicePixelRatio || 1, 1.5)) { this.renderer.setPixelRatio(Math.min(1.5, pr + 0.1)); this.resize(); }
  }
}

export function hourOf(tod) { return { DAWN: 6.2, DAY: 12.5, EVENING: 17.6, NIGHT: 23.5, DYNAMIC: 5.5 }[tod] ?? 12.5; }
