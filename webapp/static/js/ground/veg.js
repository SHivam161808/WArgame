// Vegetation & rocks: CC0 scanned props (instanced, per-biome rules) around the camera plus
// procedural wind-animated grass close to the viewer.
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';

const GT = ['ROAD', 'GRASS', 'SAND', 'MUD', 'SNOW', 'ICE', 'ROCK', 'SALT', 'MARSH', 'WATER'];
// prop, probability per 25 m candidate cell, allowed ground types, max slope (deg), scale range, radius (m)
const RULES = {
  DESERT: [
    { prop: 'shrub_dry', p: 0.035, ground: ['SAND', 'ROCK'], slope: 20, scale: [0.5, 1.0], r: 1600 },
    { prop: 'tree_dead', p: 0.008, ground: ['SAND'], slope: 10, scale: [0.6, 1.0], r: 1800 },
    { prop: 'rock_boulder', p: 0.012, ground: ['ROCK', 'SAND'], slope: 40, scale: [0.4, 1.4], r: 1400 },
  ],
  MONSOON: [
    { prop: 'tree_broad', p: 0.03, ground: ['GRASS'], slope: 20, scale: [0.7, 1.1], r: 1300, cap: 300 },
    { prop: 'grass_tuft', p: 0.08, ground: ['GRASS', 'MARSH'], slope: 25, scale: [0.8, 1.4], r: 500 },
    { prop: 'shrub_dry', p: 0.01, ground: ['MUD'], slope: 20, scale: [0.5, 0.8], r: 1200 },
  ],
  MOUNTAIN: [
    { prop: 'tree_fir', p: 0.09, ground: ['GRASS', 'MUD'], slope: 38, scale: [0.6, 1.2], r: 1100, cap: 320 },
    { prop: 'rock_coast', p: 0.02, ground: ['ROCK'], slope: 90, scale: [0.4, 1.0], r: 1500 },
    { prop: 'rock_boulder', p: 0.015, ground: ['GRASS', 'ROCK'], slope: 45, scale: [0.5, 1.5], r: 1200 },
    { prop: 'grass_tuft', p: 0.05, ground: ['GRASS'], slope: 30, scale: [0.8, 1.3], r: 400 },
  ],
  ICE: [
    { prop: 'tree_fir', p: 0.01, ground: ['SNOW'], slope: 25, scale: [0.4, 0.8], r: 1100, cap: 120 },
    { prop: 'rock_boulder', p: 0.025, ground: ['ROCK', 'SNOW'], slope: 60, scale: [0.6, 1.8], r: 1500 },
    { prop: 'rock_coast', p: 0.015, ground: ['ROCK'], slope: 90, scale: [0.5, 1.2], r: 1500 },
  ],
  MARSH: [
    { prop: 'grass_tuft', p: 0.07, ground: ['MARSH', 'MUD'], slope: 20, scale: [0.9, 1.6], r: 600 },
    { prop: 'shrub_dry', p: 0.012, ground: ['SALT', 'MUD'], slope: 20, scale: [0.5, 0.9], r: 1400 },
    { prop: 'tree_dead', p: 0.004, ground: ['MUD', 'SALT'], slope: 15, scale: [0.6, 0.9], r: 1600 },
  ],
};
const GRASS = {
  DESERT: { density: 0.25, color: [0.62, 0.55, 0.36], height: 0.45, ground: ['SAND'] },
  MONSOON: { density: 1.0, color: [0.32, 0.48, 0.18], height: 0.7, ground: ['GRASS', 'MARSH'] },
  MOUNTAIN: { density: 0.8, color: [0.4, 0.5, 0.22], height: 0.5, ground: ['GRASS'] },
  ICE: null,
  MARSH: { density: 0.7, color: [0.5, 0.52, 0.3], height: 0.9, ground: ['MARSH', 'MUD'] },
};

/** gltfpack stores quantized (int16/int8, normalized) attributes; baking a transform into them overflows. */
function toFloat(src) {
  const geo = new THREE.BufferGeometry();
  for (const [name, a] of Object.entries(src.attributes)) {
    const k = a.itemSize, arr = new Float32Array(a.count * k);
    for (let i = 0; i < a.count; i++) {
      arr[i * k] = a.getX(i);
      if (k > 1) arr[i * k + 1] = a.getY(i);
      if (k > 2) arr[i * k + 2] = a.getZ(i);
      if (k > 3) arr[i * k + 3] = a.getW(i);
    }
    geo.setAttribute(name, new THREE.BufferAttribute(arr, k));
  }
  if (src.index) geo.setIndex(src.index.clone());
  return geo;
}

function hash2(i, j, s) {
  let n = (i * 73856093) ^ (j * 19349663) ^ (s * 83492791);
  n = Math.imul(n ^ (n >>> 13), 1274126177);
  return ((n ^ (n >>> 16)) >>> 0) / 4294967296;
}

export class Vegetation {
  constructor(scene, terrain, data, quality = 1) {
    this.scene = scene;
    this.t = terrain;
    this.meta = data.meta;
    this.groundArr = data.ground;
    this.rules = RULES[data.meta.biome] || [];
    this.grassDef = GRASS[data.meta.biome];
    this.quality = quality;
    this.templates = {};
    this.meshes = [];
    this.loader = new GLTFLoader().setMeshoptDecoder(MeshoptDecoder);
    // road mask pixels so props stay off the tracks
    const c = terrain.roadMask.image;
    this.roadPx = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
    this.roadW = c.width;
  }

  async load() {
    const names = [...new Set(this.rules.map((r) => r.prop))];
    await Promise.all(names.map(async (n) => {
      const g = await this.loader.loadAsync(`/static/props/${n}.glb`);
      g.scene.updateMatrixWorld(true);
      const parts = [];
      const box = new THREE.Box3().setFromObject(g.scene);
      g.scene.traverse((o) => {
        if (!o.isMesh) return;
        const geo = toFloat(o.geometry).applyMatrix4(o.matrixWorld);
        const mat = o.material;
        if (mat.map) mat.map.anisotropy = 4;
        mat.side = THREE.DoubleSide;
        if (mat.transparent) { mat.transparent = false; mat.alphaTest = 0.45; }
        parts.push({ geo, mat });
      });
      this.templates[n] = { parts, minY: box.min.y };
    }));
    if (this.grassDef) this.makeGrassTemplate();
  }

  ground(x, y) {
    const m = this.meta;
    const i = Math.round((x - m.x0) / m.cell_m), j = Math.round((y - m.y0) / m.cell_m);
    if (i < 0 || j < 0 || i >= m.cols || j >= m.rows) return 'SAND';
    return GT[this.groundArr[j * m.cols + i]];
  }
  onRoad(x, y) {
    const e = this.t.ext;
    const u = Math.floor(((x - e.x0) / e.w) * this.roadW), v = Math.floor((1 - (y - e.y0) / e.h) * this.roadW);
    if (u < 0 || v < 0 || u >= this.roadW || v >= this.roadW) return false;
    return this.roadPx[(v * this.roadW + u) * 4] > 40;
  }
  slopeDeg(x, y) {
    const h = this.t.field.grid, d = 15;
    const gx = (h(x + d, y) - h(x - d, y)) / (2 * d), gy = (h(x, y + d) - h(x, y - d)) / (2 * d);
    return Math.atan(Math.hypot(gx, gy)) * 57.2958;
  }

  /** Re-scatter props around (cx, cy). Deterministic: same spot → same tree. */
  scatter(cx, cy) {
    for (const m of this.meshes) { this.scene.remove(m); m.dispose(); }
    this.meshes = [];
    const wl = this.meta.water_level;
    const step = 25;
    for (const [ri, rule] of this.rules.entries()) {
      const tpl = this.templates[rule.prop];
      if (!tpl) continue;
      const R = rule.r * (0.6 + 0.4 * this.quality);
      const mats = [];
      const i0 = Math.floor((cx - R) / step), i1 = Math.ceil((cx + R) / step);
      const j0 = Math.floor((cy - R) / step), j1 = Math.ceil((cy + R) / step);
      const cap = (rule.cap || 400) * this.quality;
      const m4 = new THREE.Matrix4(), q = new THREE.Quaternion(), s = new THREE.Vector3(), p = new THREE.Vector3(), up = new THREE.Vector3(0, 1, 0);
      for (let i = i0; i <= i1 && mats.length < cap; i++) for (let j = j0; j <= j1; j++) {
        if (hash2(i, j, ri + 1) > rule.p) continue;
        const x = i * step + (hash2(i, j, 91) - 0.5) * step, y = j * step + (hash2(i, j, 92) - 0.5) * step;
        if ((x - cx) ** 2 + (y - cy) ** 2 > R * R) continue;
        if (!rule.ground.includes(this.ground(x, y)) || this.onRoad(x, y) || (this.exclude && this.exclude(x, y))) continue;
        if (this.slopeDeg(x, y) > rule.slope) continue;
        const h = this.t.height(x, y);
        if (wl != null && h < wl + 0.2 && rule.prop !== 'grass_tuft') continue;
        const sc = rule.scale[0] + (rule.scale[1] - rule.scale[0]) * hash2(i, j, 93);
        q.setFromAxisAngle(up, hash2(i, j, 94) * 6.283);
        s.set(sc, sc, sc);
        p.set(x, h - tpl.minY * sc - 0.15, -y);
        mats.push(m4.compose(p, q, s).clone());
        if (mats.length >= cap) break;
      }
      if (!mats.length) continue;
      for (const part of tpl.parts) {
        const im = new THREE.InstancedMesh(part.geo, part.mat, mats.length);
        mats.forEach((mm, k) => im.setMatrixAt(k, mm));
        // only small props cast shadows: big instanced trees/rocks in the shadow pass cost too much
        im.castShadow = rule.prop === 'shrub_dry' || rule.prop === 'tree_dead';
        im.receiveShadow = true;
        im.computeBoundingSphere();
        this.scene.add(im);
        this.meshes.push(im);
      }
    }
  }

  // ── procedural grass ──────────────────────────────────────────────────────
  makeGrassTemplate() {
    const d = this.grassDef;
    // crossed quads with a blade-texture drawn on a canvas
    const c = document.createElement('canvas');
    c.width = 128; c.height = 128;
    const g = c.getContext('2d');
    for (let i = 0; i < 26; i++) {
      const x = 8 + Math.random() * 112, h = 50 + Math.random() * 75, w = 2 + Math.random() * 3, lean = (Math.random() - 0.5) * 30;
      const grad = g.createLinearGradient(0, 128, 0, 128 - h);
      grad.addColorStop(0, `rgba(${d.color.map((v) => Math.round(v * 230)).join(',')},1)`);
      grad.addColorStop(1, `rgba(${d.color.map((v) => Math.round(Math.min(255, v * 420))).join(',')},1)`);
      g.fillStyle = grad;
      g.beginPath(); g.moveTo(x - w, 128); g.quadraticCurveTo(x + lean * 0.4, 128 - h * 0.6, x + lean, 128 - h); g.lineTo(x + w, 128); g.fill();
    }
    const tex = new THREE.CanvasTexture(c);
    tex.colorSpace = THREE.SRGBColorSpace;
    const geo = new THREE.BufferGeometry();
    const H = d.height, W = 0.9;
    const v = [], uv = [], idx = [];
    for (let k = 0; k < 2; k++) {
      const a = k * Math.PI / 2, cx = Math.cos(a) * W / 2, cz = Math.sin(a) * W / 2, b = v.length / 3;
      v.push(-cx, 0, -cz, cx, 0, cz, cx, H, cz, -cx, H, -cz);
      uv.push(0, 0, 1, 0, 1, 1, 0, 1);
      idx.push(b, b + 1, b + 2, b, b + 2, b + 3);
    }
    geo.setAttribute('position', new THREE.Float32BufferAttribute(v, 3));
    geo.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2));
    geo.setIndex(idx);
    geo.computeVertexNormals();
    const mat = new THREE.MeshStandardMaterial({ map: tex, alphaTest: 0.5, side: THREE.DoubleSide, roughness: 0.95 });
    this.windTime = { value: 0 };
    mat.onBeforeCompile = (sh) => {
      sh.uniforms.uTime = this.windTime;
      sh.vertexShader = sh.vertexShader.replace('#include <common>', '#include <common>\nuniform float uTime;')
        .replace('#include <begin_vertex>', `#include <begin_vertex>
          vec4 wp = instanceMatrix * vec4(0.0,0.0,0.0,1.0);
          float sway = sin(uTime*1.7 + wp.x*0.15 + wp.z*0.11) * 0.18 + sin(uTime*3.1 + wp.x*0.7) * 0.05;
          transformed.x += sway * position.y; transformed.z += sway * 0.6 * position.y;`);
      // grass normals point up so lighting is soft and uniform
      sh.vertexShader = sh.vertexShader.replace('#include <beginnormal_vertex>', 'vec3 objectNormal = vec3(0.0,1.0,0.0);');
    };
    this.grassGeo = geo;
    this.grassMat = mat;
    this.grassMesh = null;
  }

  grass(cx, cy, time) {
    if (!this.grassDef || !this.grassGeo) return; // grass texture still loading
    this.windTime.value = time;
    if (this.grassAt && Math.hypot(cx - this.grassAt[0], cy - this.grassAt[1]) < 30) return;
    this.grassAt = [cx, cy];
    const R = 150 * (0.6 + 0.4 * this.quality), step = 2.2 / Math.sqrt(this.grassDef.density);
    const maxN = Math.floor(60000 * this.quality);
    if (!this.grassMesh) {
      this.grassMesh = new THREE.InstancedMesh(this.grassGeo, this.grassMat, maxN);
      this.grassMesh.frustumCulled = false;
      this.grassMesh.receiveShadow = true;
      this.scene.add(this.grassMesh);
    }
    const m4 = new THREE.Matrix4(), q = new THREE.Quaternion(), s = new THREE.Vector3(), p = new THREE.Vector3(), up = new THREE.Vector3(0, 1, 0);
    let n = 0;
    const i0 = Math.floor((cx - R) / step), i1 = Math.ceil((cx + R) / step), j0 = Math.floor((cy - R) / step), j1 = Math.ceil((cy + R) / step);
    const wl = this.meta.water_level;
    for (let i = i0; i <= i1 && n < maxN; i++) for (let j = j0; j <= j1 && n < maxN; j++) {
      const x = i * step + (hash2(i, j, 7) - 0.5) * step * 1.6, y = j * step + (hash2(i, j, 8) - 0.5) * step * 1.6;
      const dd = (x - cx) ** 2 + (y - cy) ** 2;
      if (dd > R * R) continue;
      if (hash2(i, j, 9) > this.grassDef.density) continue;
      if (!this.grassDef.ground.includes(this.ground(x, y)) || this.onRoad(x, y)) continue;
      const h = this.t.height(x, y);
      if (wl != null && h < wl - 0.3) continue;
      const sc = 0.7 + hash2(i, j, 10) * 0.8;
      q.setFromAxisAngle(up, hash2(i, j, 11) * 6.283);
      s.set(sc, sc * (0.7 + hash2(i, j, 12) * 0.6), sc);
      p.set(x, h - 0.05, -y);
      this.grassMesh.setMatrixAt(n++, m4.compose(p, q, s));
    }
    this.grassMesh.count = n;
    this.grassMesh.instanceMatrix.needsUpdate = true;
  }
}
