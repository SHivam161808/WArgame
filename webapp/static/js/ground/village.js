// Village Skirmish: 3D village built from the server's layout (same wall pieces the simulation uses for
// collision, line of sight and bullets), parked-vehicle models, loot on the floor, and a JS port of the
// wall collision / ray tests for client-side prediction, camera collision and aiming.
import * as THREE from 'three';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { toThree } from './fx.js';

const HASH = 8;
const BODY_R = 0.3;
const BLOCK_Z = 1.0;

// ── procedural textures ─────────────────────────────────────────────────────
function canvasTex(w, h, draw, repeat = true) {
  const c = document.createElement('canvas');
  c.width = w; c.height = h;
  draw(c.getContext('2d'), w, h);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  if (repeat) t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.anisotropy = 8;
  return t;
}
function noiseFill(g, w, h, base, amt, n = 2600) {
  g.fillStyle = base; g.fillRect(0, 0, w, h);
  for (let i = 0; i < n; i++) {
    const v = Math.random();
    g.fillStyle = v < 0.5 ? `rgba(0,0,0,${amt * Math.random()})` : `rgba(255,255,255,${amt * 0.7 * Math.random()})`;
    const s = 1 + Math.random() * 3;
    g.fillRect(Math.random() * w, Math.random() * h, s, s);
  }
}
const TEX = {};
function plaster() {
  return TEX.plaster ||= canvasTex(256, 256, (g, w, h) => {
    noiseFill(g, w, h, '#ffffff', 0.13, 4000);
    g.strokeStyle = 'rgba(0,0,0,0.06)';
    for (let i = 0; i < 14; i++) { g.beginPath(); g.moveTo(Math.random() * w, Math.random() * h); g.lineTo(Math.random() * w, Math.random() * h); g.stroke(); }
    const grad = g.createLinearGradient(0, h * 0.75, 0, h); // damp stain at the foot of walls
    grad.addColorStop(0, 'rgba(60,40,20,0)'); grad.addColorStop(1, 'rgba(60,40,20,0.18)');
    g.fillStyle = grad; g.fillRect(0, 0, w, h);
  });
}
function brick() {
  return TEX.brick ||= canvasTex(256, 256, (g, w, h) => {
    g.fillStyle = '#b8a48c'; g.fillRect(0, 0, w, h);
    const bh = 16, bw = 42;
    for (let r = 0; r < h / bh; r++) for (let c = -1; c < w / bw + 1; c++) {
      const x = c * bw + (r % 2 ? bw / 2 : 0), y = r * bh;
      const k = 0.75 + Math.random() * 0.35;
      g.fillStyle = `rgb(${Math.round(160 * k)},${Math.round(78 * k)},${Math.round(52 * k)})`;
      g.fillRect(x + 1, y + 1, bw - 2, bh - 2);
    }
  });
}
function concrete() {
  return TEX.concrete ||= canvasTex(256, 256, (g, w, h) => noiseFill(g, w, h, '#a9a49b', 0.15, 3000));
}
function floorTiles() {
  return TEX.tiles ||= canvasTex(256, 256, (g, w, h) => {
    const n = 4, s = w / n;
    for (let i = 0; i < n; i++) for (let j = 0; j < n; j++) {
      g.fillStyle = (i + j) % 2 ? '#8f6e55' : '#c9b79b'; g.fillRect(i * s, j * s, s, s);
    }
    g.strokeStyle = 'rgba(0,0,0,.25)'; for (let i = 0; i <= n; i++) { g.beginPath(); g.moveTo(i * s, 0); g.lineTo(i * s, h); g.moveTo(0, i * s); g.lineTo(w, i * s); g.stroke(); }
  });
}
function hayTex() {
  return TEX.hay ||= canvasTex(128, 128, (g, w, h) => {
    g.fillStyle = '#c9a443'; g.fillRect(0, 0, w, h);
    for (let i = 0; i < 900; i++) {
      g.strokeStyle = `rgba(${120 + Math.random() * 100},${90 + Math.random() * 70},20,0.6)`;
      const x = Math.random() * w, y = Math.random() * h; g.beginPath(); g.moveTo(x, y); g.lineTo(x + (Math.random() - 0.5) * 10, y + Math.random() * 12); g.stroke();
    }
  });
}
function signTex(text) {
  return canvasTex(1024, 128, (g, w, h) => {
    g.fillStyle = '#1d4f91'; g.fillRect(0, 0, w, h);
    g.strokeStyle = '#ffffff'; g.lineWidth = 6; g.strokeRect(8, 8, w - 16, h - 16);
    g.fillStyle = '#fff'; g.font = 'bold 56px sans-serif'; g.textAlign = 'center'; g.textBaseline = 'middle';
    g.fillText(text, w / 2, h / 2 + 2);
  }, false);
}

// World-space (triplanar) texturing so wall textures never stretch, with per-vertex tint.
function worldMaterial(map, { scale = 0.5, rough = 0.92 } = {}) {
  const m = new THREE.MeshStandardMaterial({ map, vertexColors: true, roughness: rough, metalness: 0 });
  m.onBeforeCompile = (sh) => {
    sh.uniforms.uScale = { value: scale };
    sh.vertexShader = sh.vertexShader.replace('#include <common>', '#include <common>\nvarying vec3 vWP; varying vec3 vWN;')
      .replace('#include <worldpos_vertex>', '#include <worldpos_vertex>\nvWP = (modelMatrix * vec4(transformed,1.0)).xyz; vWN = normalize(mat3(modelMatrix) * objectNormal);');
    sh.fragmentShader = sh.fragmentShader.replace('#include <common>', '#include <common>\nvarying vec3 vWP; varying vec3 vWN; uniform float uScale;')
      .replace('#include <map_fragment>', `
        vec3 bw = pow(abs(vWN), vec3(4.0)); bw /= (bw.x + bw.y + bw.z);
        vec4 tx = texture2D(map, vWP.zy * uScale) * bw.x + texture2D(map, vWP.xz * uScale) * bw.y + texture2D(map, vWP.xy * uScale) * bw.z;
        diffuseColor *= tx;`);
  };
  return m;
}

// ── geometry helpers ───────────────────────────────────────────────────────
const _m4 = new THREE.Matrix4(), _q = new THREE.Quaternion(), _v = new THREE.Vector3(), _s = new THREE.Vector3(), _up = new THREE.Vector3(0, 1, 0);
function boxAt(list, colour, cx, cy, z0, sx, sz, sy, yaw) {
  // sim coords: (cx, cy) centre, z0 bottom, size along local x = sx, height sz, local y (depth) = sy, yaw = rotation (rad, CCW)
  const g = new THREE.BoxGeometry(sx, sz, sy);
  _q.setFromAxisAngle(_up, yaw);
  _m4.compose(_v.set(cx, z0 + sz / 2, -cy), _q, _s.set(1, 1, 1));
  g.applyMatrix4(_m4);
  const n = g.attributes.position.count;
  const col = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) col.set([colour.r, colour.g, colour.b], i * 3);
  g.setAttribute('color', new THREE.BufferAttribute(col, 3));
  list.push(g);
}
function mergedMesh(list, mat, { cast = true, receive = true } = {}) {
  if (!list.length) return null;
  const geo = mergeGeometries(list.map((g) => (g.index ? g.toNonIndexed() : g)), false);
  list.forEach((g) => g.dispose());
  geo.computeBoundingSphere();
  const m = new THREE.Mesh(geo, mat);
  m.castShadow = cast; m.receiveShadow = receive;
  return m;
}
const seg = (px, py, rx, ry, qx, qy, sx, sy) => {
  const den = rx * sy - ry * sx;
  if (Math.abs(den) < 1e-12) return null;
  const wx = qx - px, wy = qy - py;
  const t = (wx * sy - wy * sx) / den, u = (wx * ry - wy * rx) / den;
  return t >= 0 && t <= 1 && u >= 0 && u <= 1 ? t : null;
};
const segDist = (px, py, ax, ay, bx, by) => {
  const dx = bx - ax, dy = by - ay, l2 = dx * dx + dy * dy || 1e-9;
  const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / l2));
  const cx = ax + t * dx, cy = ay + t * dy;
  return [Math.hypot(px - cx, py - cy), cx, cy];
};
const insideRect = (x, y, b, pad = 0) => {
  const c = Math.cos(-b.rot), s = Math.sin(-b.rot);
  const lx = c * (x - b.cx) - s * (y - b.cy), ly = s * (x - b.cx) + c * (y - b.cy);
  return Math.abs(lx) <= b.w / 2 + pad && Math.abs(ly) <= b.d / 2 + pad;
};

// ── the village ─────────────────────────────────────────────────────────────
export class Village {
  constructor(scene, layout, terrain) {
    this.scene = scene;
    this.L = layout;
    this.terrain = terrain;
    this.walls = layout.walls;
    this.wallT = layout.wall_t || 0.25;
    this.cells = new Map();
    this.walls.forEach((w, k) => {
      for (let i = Math.floor((Math.min(w[0], w[2]) - 0.5) / HASH); i <= Math.floor((Math.max(w[0], w[2]) + 0.5) / HASH); i++)
        for (let j = Math.floor((Math.min(w[1], w[3]) - 0.5) / HASH); j <= Math.floor((Math.max(w[1], w[3]) + 0.5) / HASH); j++) {
          const key = i * 100003 + j;
          if (!this.cells.has(key)) this.cells.set(key, []);
          this.cells.get(key).push(k);
        }
    });
    this.bucket = new Map();
    for (const b of layout.buildings) {
      const r = Math.hypot(b.w, b.d) / 2;
      for (let i = Math.floor((b.cx - r) / 16); i <= Math.floor((b.cx + r) / 16); i++)
        for (let j = Math.floor((b.cy - r) / 16); j <= Math.floor((b.cy + r) / 16); j++) {
          const key = i * 100003 + j;
          if (!this.bucket.has(key)) this.bucket.set(key, []);
          this.bucket.get(key).push(b);
        }
    }
    this.group = new THREE.Group();
    scene.add(this.group);
    this.build();
  }

  // geometry queries (mirror sim/village.py) --------------------------------
  near(x, y, r) {
    const out = new Set();
    for (let i = Math.floor((x - r) / HASH); i <= Math.floor((x + r) / HASH); i++)
      for (let j = Math.floor((y - r) / HASH); j <= Math.floor((y + r) / HASH); j++)
        for (const k of this.cells.get(i * 100003 + j) || []) out.add(k);
    return out;
  }
  buildingAt(x, y) {
    for (const b of this.bucket.get(Math.floor(x / 16) * 100003 + Math.floor(y / 16)) || []) if (insideRect(x, y, b, 0.2)) return b;
    return null;
  }
  groundZ(x, y) { const b = this.buildingAt(x, y); return b ? b.zb : this.terrain.height(x, y); }
  collide(x0, y0, x1, y1, r = BODY_R) {
    const h = this.L.play_half;
    x1 = Math.max(-h, Math.min(h, x1)); y1 = Math.max(-h, Math.min(h, y1));
    const n = Math.max(1, Math.ceil(Math.hypot(x1 - x0, y1 - y0) / 0.25));
    let x = x0, y = y0;
    const lim = r + this.wallT / 2;
    for (let s = 0; s < n; s++) {
      x += (x1 - x0) / n; y += (y1 - y0) / n;
      for (let it = 0; it < 2; it++) for (const k of this.near(x, y, lim + 0.5)) {
        const w = this.walls[k];
        if (w[5] >= BLOCK_Z) continue;
        const [d, cx, cy] = segDist(x, y, w[0], w[1], w[2], w[3]);
        if (d < lim) {
          let ux, uy;
          if (d < 1e-6) { const L = Math.hypot(w[2] - w[0], w[3] - w[1]) || 1; ux = -(w[3] - w[1]) / L; uy = (w[2] - w[0]) / L; }
          else { ux = (x - cx) / d; uy = (y - cy) / d; }
          x = cx + ux * lim; y = cy + uy * lim;
        }
      }
    }
    return [x, y];
  }
  /** Fraction along a→b (sim coords, z ASL) where a wall is hit, or null. */
  rayT(ax, ay, az, bx, by, bz) {
    const L = Math.hypot(bx - ax, by - ay), n = Math.max(1, Math.floor(L / (HASH * 0.5)));
    const seen = new Set();
    let best = null;
    for (let s = 0; s <= n; s++) {
      const x = ax + (bx - ax) * s / n, y = ay + (by - ay) * s / n;
      for (const k of this.cells.get(Math.floor(x / HASH) * 100003 + Math.floor(y / HASH)) || []) {
        if (seen.has(k)) continue;
        seen.add(k);
        const w = this.walls[k];
        const t = seg(ax, ay, bx - ax, by - ay, w[0], w[1], w[2] - w[0], w[3] - w[1]);
        if (t === null || (best !== null && t >= best)) continue;
        const z = az + (bz - az) * t;
        if (z >= w[4] + w[5] && z <= w[4] + w[6]) best = t;
      }
    }
    return best;
  }

  // meshes ---------------------------------------------------------------
  build() {
    const L = this.L, T = this.wallT;
    const tint = (hex) => new THREE.Color(hex);
    const owner = (x, y) => this.buildingAt(x, y);
    const plasterG = [], brickG = [], concreteG = [], floorG = [], frameG = [], hayG = [], tinG = [];
    const darkWood = tint('#4a3426'), mud = tint('#8a6a4a'), white = tint('#ffffff');
    // walls: building walls take the house colour, compound walls are brick, mud walls / haystacks their own
    for (const w of L.walls) {
      const [ax, ay, bx, by, zb, z0, z1, kind] = w;
      const len = Math.hypot(bx - ax, by - ay);
      if (len < 0.02) continue;
      const yaw = Math.atan2(by - ay, bx - ax);
      const cx = (ax + bx) / 2, cy = (ay + by) / 2;
      if (kind === 2) continue; // haystacks are drawn as stacks below
      if (kind === 1) { boxAt(plasterG, mud, cx, cy, zb, len + 0.1, z1 - z0, 0.55, yaw); continue; }
      if (kind === 3) { boxAt(brickG, white, cx, cy, zb + z0, len + T, z1 - z0, T + 0.05, yaw); continue; }
      const b = owner(cx, cy);
      const col = tint(b ? b.col : '#d9c7a8');
      boxAt(plasterG, col, cx, cy, zb + z0, len + T, z1 - z0, T, yaw);
      // window / door trim: dark frames on pieces that end above the floor (sills) or start above it (lintels)
      if (z0 > 0.5) boxAt(frameG, darkWood, cx, cy, zb + z0 - 0.06, len + T, 0.08, T + 0.06, yaw);
      if (z0 === 0 && z1 < 1.2) boxAt(frameG, darkWood, cx, cy, zb + z1 - 0.04, len + T, 0.08, T + 0.08, yaw);
    }
    // floors, roofs with parapets, shed roofs
    for (const b of L.buildings) {
      const yaw = b.rot;
      boxAt(floorG, white, b.cx, b.cy, b.zb - 0.3, b.w + T, 0.3, b.d + T, yaw);
      if (b.k === 'shed') {
        // corrugated tin roof sloping back, on posts at the open front
        const g = new THREE.BoxGeometry(b.w + 0.8, 0.08, b.d + 1.2);
        _q.setFromEuler(new THREE.Euler(0, yaw, 0, 'YXZ')).multiply(new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), -0.12));
        _m4.compose(_v.set(b.cx, b.zb + b.h + 0.15, -b.cy), _q, _s.set(1, 1, 1));
        g.applyMatrix4(_m4);
        const col = new Float32Array(g.attributes.position.count * 3).fill(0.75);
        g.setAttribute('color', new THREE.BufferAttribute(col, 3));
        tinG.push(g);
        for (const sx of [-1, 1]) {
          const [px, py] = rot(yaw, sx * (b.w / 2 - 0.2), -b.d / 2 + 0.1, b.cx, b.cy);
          boxAt(frameG, darkWood, px, py, b.zb, 0.18, b.h + 0.2, 0.18, yaw);
        }
        continue;
      }
      boxAt(concreteG, white, b.cx, b.cy, b.zb + b.h, b.w + T + 0.2, 0.22, b.d + T + 0.2, yaw);
      const ph = b.k === 'school' ? 0.9 : 0.55, hw = b.w / 2 + 0.05, hd = b.d / 2 + 0.05;
      for (const [lx, ly, sx, sy] of [[0, -hd, b.w + T + 0.2, 0.18], [0, hd, b.w + T + 0.2, 0.18], [-hw, 0, 0.18, b.d + T], [hw, 0, 0.18, b.d + T]]) {
        const [px, py] = rot(yaw, lx, ly, b.cx, b.cy);
        boxAt(concreteG, tint(b.col), px, py, b.zb + b.h + 0.22, sx, ph, sy, yaw);
      }
      // little water tank on some roofs
      if (b.k !== 'school' && hashf(b.cx, b.cy) < 0.35) {
        const [px, py] = rot(yaw, b.w / 4, b.d / 5, b.cx, b.cy);
        const g = new THREE.CylinderGeometry(0.55, 0.55, 1.1, 12);
        g.translate(px, b.zb + b.h + 0.22 + 0.55, -py);
        const col = new Float32Array(g.attributes.position.count * 3);
        for (let i = 0; i < g.attributes.position.count; i++) col.set([0.08, 0.08, 0.08], i * 3);
        g.setAttribute('color', new THREE.BufferAttribute(col, 3));
        concreteG.push(g.toNonIndexed());
      }
    }
    // haystacks
    for (const p of L.props) {
      if (p.k !== 'hay') continue;
      const g = new THREE.CylinderGeometry(p.r * 0.85, p.r, 1.6, 14);
      g.translate(p.x, p.zb + 0.8, -p.y);
      const cone = new THREE.ConeGeometry(p.r * 0.9, 1.0, 14);
      cone.translate(p.x, p.zb + 2.1, -p.y);
      for (const gg of [g, cone]) {
        const col = new Float32Array(gg.attributes.position.count * 3).fill(1);
        gg.setAttribute('color', new THREE.BufferAttribute(col, 3));
        hayG.push(gg.toNonIndexed());
      }
    }
    const add = (m) => { if (m) { this.group.add(m); return m; } return null; };
    this.wallMesh = add(mergedMesh(plasterG, worldMaterial(plaster(), { scale: 0.45 })));
    add(mergedMesh(brickG, worldMaterial(brick(), { scale: 0.6 })));
    add(mergedMesh(concreteG, worldMaterial(concrete(), { scale: 0.3 })));
    add(mergedMesh(floorG, worldMaterial(floorTiles(), { scale: 0.5 }), { cast: false }));
    add(mergedMesh(frameG, new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.8 })));
    add(mergedMesh(hayG, worldMaterial(hayTex(), { scale: 0.7 })));
    add(mergedMesh(tinG, new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.45, metalness: 0.6, color: 0x9aa3a8 })));
    this.decor();
  }

  decor() {
    const L = this.L;
    // school: signboard over the middle door, flag pole in the yard
    const school = L.buildings.find((b) => b.k === 'school');
    if (school) {
      const sign = new THREE.Mesh(new THREE.PlaneGeometry(14, 1.6), new THREE.MeshStandardMaterial({ map: signTex('GOVT. SENIOR SECONDARY SCHOOL'), roughness: 0.6 }));
      const [sx, sy] = rot(school.rot, 0, -school.d / 2 - 0.2, school.cx, school.cy);
      sign.position.copy(toThree([sx, sy, school.zb + school.h + 0.45]));
      sign.rotation.y = school.rot + Math.PI;
      sign.rotation.y = -Math.atan2(-Math.cos(school.rot), -Math.sin(school.rot)) + Math.PI / 2;
      this.group.add(sign);
      const cp = school.compound;
      if (cp) {
        const [fx, fy] = rot(cp.rot, -cp.w / 2 + 6, -cp.d / 2 + 6, cp.cx, cp.cy);
        const z = cp.zb;
        const pole = new THREE.Mesh(new THREE.CylinderGeometry(0.07, 0.09, 9, 8), new THREE.MeshStandardMaterial({ color: 0xdddddd, metalness: 0.7, roughness: 0.3 }));
        pole.position.copy(toThree([fx, fy, z + 4.5])); pole.castShadow = true;
        this.group.add(pole);
        const flag = new THREE.Mesh(new THREE.PlaneGeometry(2.4, 1.6, 12, 1), new THREE.MeshStandardMaterial({ map: flagTex(), side: THREE.DoubleSide, roughness: 0.8 }));
        flag.position.copy(toThree([fx, fy, z + 8.0])); flag.position.x += 1.25;
        this.flag = flag;
        this.group.add(flag);
      }
    }
    // electric poles with sagging wires along the main road
    const main = L.roads[0];
    const poleMat = new THREE.MeshStandardMaterial({ color: 0x8f8a80, roughness: 0.9 });
    const poles = [];
    for (let k = 2; k < main.path.length - 2; k += 4) {
      const [x, y] = main.path[k];
      if (Math.abs(x) > L.play_half + 150) continue;
      const [a, b] = main.path[k - 1], [c, d] = main.path[k + 1];
      const tx = c - a, ty = d - b, n = Math.hypot(tx, ty) || 1;
      const px = x - ty / n * (main.width / 2 + 1.2), py = y + tx / n * (main.width / 2 + 1.2);
      if (this.buildingAt(px, py)) continue;
      poles.push([px, py, this.terrain.height(px, py)]);
    }
    if (poles.length) {
      const im = new THREE.InstancedMesh(new THREE.CylinderGeometry(0.11, 0.15, 8, 6), poleMat, poles.length);
      poles.forEach(([x, y, z], i) => { _m4.compose(_v.copy(toThree([x, y, z + 4])), _q.identity(), _s.set(1, 1, 1)); im.setMatrixAt(i, _m4); });
      im.castShadow = true;
      this.group.add(im);
      const pts = [];
      for (let i = 0; i < poles.length - 1; i++) {
        const [x0, y0, z0] = poles[i], [x1, y1, z1] = poles[i + 1];
        if (Math.hypot(x1 - x0, y1 - y0) > 60) continue;
        for (const off of [-0.5, 0.5]) for (let s = 0; s < 8; s++) {
          const t0 = s / 8, t1 = (s + 1) / 8;
          const sag = (t) => -Math.sin(Math.PI * t) * 0.9;
          pts.push(toThree([x0 + (x1 - x0) * t0 + off * 0.3, y0 + (y1 - y0) * t0, z0 + 7.7 + (z1 - z0) * t0 + sag(t0)]));
          pts.push(toThree([x0 + (x1 - x0) * t1 + off * 0.3, y0 + (y1 - y0) * t1, z0 + 7.7 + (z1 - z0) * t1 + sag(t1)]));
        }
      }
      this.group.add(new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({ color: 0x222222 })));
    }
    // charpai cots, drums and crates outside some houses (decoration)
    const drum = new THREE.CylinderGeometry(0.3, 0.3, 0.9, 10), crate = new THREE.BoxGeometry(0.8, 0.6, 0.8);
    const drums = [], crates = [];
    for (const b of L.buildings) {
      if (b.k === 'school') continue;
      const h = hashf(b.cx * 1.3, b.cy * 0.7);
      if (h > 0.55) continue;
      const [x, y] = rot(b.rot, b.w / 2 - 0.8, -b.d / 2 - 1.3, b.cx, b.cy);
      (h < 0.3 ? drums : crates).push([x, y, this.terrain.height(x, y), b.rot]);
    }
    for (const [list, geo, color, hgt] of [[drums, drum, 0x2f5d8a, 0.45], [crates, crate, 0x8a6b45, 0.3]]) {
      if (!list.length) continue;
      const im = new THREE.InstancedMesh(geo, new THREE.MeshStandardMaterial({ color, roughness: 0.7, metalness: list === drums ? 0.4 : 0 }), list.length);
      list.forEach(([x, y, z, r], i) => { _q.setFromAxisAngle(_up, r); _m4.compose(_v.copy(toThree([x, y, z + hgt])), _q, _s.set(1, 1, 1)); im.setMatrixAt(i, _m4); });
      im.castShadow = true; im.receiveShadow = true;
      this.group.add(im);
    }
    // play-area boundary: a faint red line of posts
    const h = L.play_half, postPts = [];
    for (let s = -h; s <= h; s += 25) for (const [x, y] of [[s, -h], [s, h], [-h, s], [h, s]]) postPts.push([x, y, this.terrain.height(x, y)]);
    const im = new THREE.InstancedMesh(new THREE.CylinderGeometry(0.08, 0.08, 1.4, 5), new THREE.MeshBasicMaterial({ color: 0xff4b3e }), postPts.length);
    postPts.forEach(([x, y, z], i) => { _m4.compose(_v.copy(toThree([x, y, z + 0.7])), _q.identity(), _s.set(1, 1, 1)); im.setMatrixAt(i, _m4); });
    this.group.add(im);
  }

  update(t) {
    if (this.flag) {
      const p = this.flag.geometry.attributes.position;
      for (let i = 0; i < p.count; i++) { const x = p.getX(i) + 1.2; p.setZ(i, Math.sin(t * 4 + x * 2.2) * 0.12 * x); }
      p.needsUpdate = true;
    }
  }
}

function flagTex() {
  return canvasTex(240, 160, (g, w, h) => {
    g.fillStyle = '#ff9933'; g.fillRect(0, 0, w, h / 3);
    g.fillStyle = '#ffffff'; g.fillRect(0, h / 3, w, h / 3);
    g.fillStyle = '#138808'; g.fillRect(0, 2 * h / 3, w, h / 3);
    g.strokeStyle = '#000080'; g.lineWidth = 2;
    g.beginPath(); g.arc(w / 2, h / 2, h / 7, 0, Math.PI * 2); g.stroke();
    for (let k = 0; k < 24; k++) { const a = k / 24 * Math.PI * 2; g.beginPath(); g.moveTo(w / 2, h / 2); g.lineTo(w / 2 + Math.cos(a) * h / 7, h / 2 + Math.sin(a) * h / 7); g.stroke(); }
  }, false);
}
function rot(r, lx, ly, cx, cy) { const c = Math.cos(r), s = Math.sin(r); return [cx + c * lx - s * ly, cy + s * lx + c * ly]; }
function hashf(x, y) { const s = Math.sin(x * 12.9898 + y * 78.233) * 43758.5453; return s - Math.floor(s); }

// ── civilian vehicles (procedural; faces +Z like the glTF models) ─────────────
const CAR_COLOURS = [0xc8102e, 0xf2f2f2, 0x1f4e8c, 0x2b2b2b, 0xd9a400, 0x3c7a3c];
export function makeVehicle(kind, seed = 0) {
  const g = new THREE.Group();
  const paint = new THREE.MeshStandardMaterial({ color: CAR_COLOURS[Math.abs(seed) % CAR_COLOURS.length], roughness: 0.35, metalness: 0.55 });
  const dark = new THREE.MeshStandardMaterial({ color: 0x1a1a1a, roughness: 0.6 });
  const glass = new THREE.MeshStandardMaterial({ color: 0x223344, roughness: 0.1, metalness: 0.8, transparent: true, opacity: 0.75 });
  const chrome = new THREE.MeshStandardMaterial({ color: 0xbbbbbb, roughness: 0.25, metalness: 0.9 });
  const olive = new THREE.MeshStandardMaterial({ color: 0x4f5a34, roughness: 0.8 });
  const box = (w, h, d, mat, x, y, z) => { const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat); m.position.set(x, y, z); m.castShadow = true; m.receiveShadow = true; g.add(m); return m; };
  const wheel = (r, w, x, y, z) => { const m = new THREE.Mesh(new THREE.CylinderGeometry(r, r, w, 16), dark); m.rotation.z = Math.PI / 2; m.position.set(x, y, z); m.castShadow = true; g.add(m); return m; };
  if (kind === 'car') {
    box(1.75, 0.6, 4.1, paint, 0, 0.55, 0);
    box(1.6, 0.55, 2.1, glass, 0, 1.1, -0.15);
    box(1.62, 0.06, 2.0, paint, 0, 1.4, -0.15);
    box(1.7, 0.18, 0.1, chrome, 0, 0.45, 2.06); box(1.7, 0.18, 0.1, chrome, 0, 0.45, -2.06);
    for (const [x, z] of [[-0.82, 1.3], [0.82, 1.3], [-0.82, -1.3], [0.82, -1.3]]) wheel(0.32, 0.22, x, 0.32, z);
    for (const x of [-0.6, 0.6]) { const l = box(0.3, 0.12, 0.05, new THREE.MeshStandardMaterial({ color: 0xffffee, emissive: 0xffffcc, emissiveIntensity: 0.6 }), x, 0.65, 2.08); l.castShadow = false; }
  } else if (kind === 'bike') {
    wheel(0.33, 0.1, 0, 0.33, 0.72); wheel(0.33, 0.1, 0, 0.33, -0.72);
    box(0.25, 0.32, 0.9, paint, 0, 0.72, 0.05);
    box(0.28, 0.12, 0.7, dark, 0, 0.9, -0.35);
    box(0.7, 0.05, 0.05, chrome, 0, 1.08, 0.6);
    box(0.06, 0.45, 0.06, chrome, 0, 0.85, 0.66);
    box(0.08, 0.08, 0.6, chrome, 0.14, 0.45, -0.3);
  } else if (kind === 'jeep') {
    box(1.8, 0.75, 4.0, olive, 0, 0.75, 0);
    box(1.7, 0.08, 1.0, glass, 0, 1.45, 0.9).rotation.x = -0.25;
    box(1.8, 0.1, 2.2, olive, 0, 1.12, -0.6);
    for (const [x, z] of [[-0.85, 1.25], [0.85, 1.25], [-0.85, -1.25], [0.85, -1.25]]) wheel(0.4, 0.3, x, 0.4, z);
    wheel(0.38, 0.28, 0, 1.0, -2.1).rotation.set(0, 0, 0);
    box(0.12, 0.6, 0.12, dark, 0, 1.5, -0.8);
    const gun = box(0.12, 0.12, 1.3, dark, 0, 1.85, -0.4); gun.name = 'gun';
  }
  return g;
}

// ── loot on the floor ──────────────────────────────────────────────────────
const ITEM_COLOURS = { ammo: 0x4f7a2f, medkit: 0xf2f2f2, vest: 0x2f3b2a, helmet: 0x3b4a2a };
export class Loot {
  constructor(scene, kit, catalogue) {
    this.scene = scene; this.kit = kit; this.cat = catalogue;
    this.items = new Map();
    this.group = new THREE.Group();
    scene.add(this.group);
    this.ring = new THREE.RingGeometry(0.35, 0.42, 20).rotateX(-Math.PI / 2);
    this.ringMat = new THREE.MeshBasicMaterial({ color: 0xffd34d, transparent: true, opacity: 0.55, depthWrite: false });
  }
  label(k) {
    const w = this.cat.weapons[k];
    return w ? w.name : { ammo: 'Ammunition', medkit: 'Medkit', vest: 'Body armour', helmet: 'Helmet' }[k] || k;
  }
  make(k) {
    const g = new THREE.Group();
    const w = this.cat.weapons[k];
    const gun = w && this.kit?.guns?.[w.model];
    if (gun) {
      const m = gun.clone(true);
      m.position.set(0, 0, 0); m.quaternion.identity(); m.scale.set(1, 1, 1);
      m.updateMatrixWorld(true);
      const bb = new THREE.Box3().setFromObject(m, true), sz = bb.getSize(new THREE.Vector3());
      const want = { Pistol: 0.25, SMG: 0.55, AK: 0.85, Sniper: 1.15, Sniper_2: 1.0, Shotgun: 0.95, RocketLauncher: 1.0 }[w.model] || 0.8;
      const s = want / Math.max(sz.x, sz.y, sz.z, 1e-6);
      m.scale.setScalar(s);
      const c = bb.getCenter(new THREE.Vector3()).multiplyScalar(s);
      m.position.set(-c.x, -bb.min.y * s + 0.03, -c.z);
      const holder = new THREE.Group(); holder.add(m); holder.rotation.z = Math.PI / 2 * 0; g.add(holder);
    } else {
      const mat = new THREE.MeshStandardMaterial({ color: ITEM_COLOURS[k] || 0x888888, roughness: 0.6 });
      let mesh;
      if (k === 'helmet') mesh = new THREE.Mesh(new THREE.SphereGeometry(0.17, 14, 8, 0, Math.PI * 2, 0, Math.PI / 2), mat);
      else if (k === 'vest') mesh = new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.12, 0.6), mat);
      else if (k === 'medkit') {
        mesh = new THREE.Mesh(new THREE.BoxGeometry(0.4, 0.15, 0.3), mat);
        const cross = new THREE.MeshBasicMaterial({ color: 0xd61f1f });
        const a = new THREE.Mesh(new THREE.BoxGeometry(0.2, 0.01, 0.06), cross), b = new THREE.Mesh(new THREE.BoxGeometry(0.06, 0.01, 0.2), cross);
        a.position.y = b.position.y = 0.08; mesh.add(a, b);
      } else mesh = new THREE.Mesh(new THREE.BoxGeometry(0.35, 0.22, 0.25), mat);
      mesh.position.y = k === 'helmet' ? 0.02 : 0.08;
      mesh.castShadow = true;
      g.add(mesh);
    }
    const ring = new THREE.Mesh(this.ring, this.ringMat);
    ring.position.y = 0.04;
    g.add(ring);
    return g;
  }
  sync(list) {
    const seen = new Set();
    for (const [id, k, x, y, z] of list) {
      seen.add(id);
      let it = this.items.get(id);
      if (!it) {
        const obj = this.make(k);
        obj.position.copy(toThree([x, y, z]));
        obj.rotation.y = hashf(x, y) * 6.28;
        this.group.add(obj);
        it = { id, k, x, y, z, obj };
        this.items.set(id, it);
      }
    }
    for (const [id, it] of this.items) if (!seen.has(id)) { this.group.remove(it.obj); this.items.delete(id); }
  }
  nearest(x, y, z, r = 2.4) {
    let best = null, bd = r;
    for (const it of this.items.values()) {
      const d = Math.hypot(it.x - x, it.y - y);
      if (d < bd && Math.abs(it.z - z) < 1.6) { best = it; bd = d; }
    }
    return best;
  }
}
