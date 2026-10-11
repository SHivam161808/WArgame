// Ground-level effects: pooled GPU particles (fire/smoke/dust/sparks), tracer & beam lines,
// and flash lights for muzzle fire and explosions.
import * as THREE from 'three';

const V = (p) => new THREE.Vector3(p[0], p[2], -p[1]); // local [x,y,z] → three

class ParticlePool {
  constructor(scene, max, blending, soft) {
    this.max = max;
    this.n = 0;
    this.pos = new Float32Array(max * 3);
    this.col = new Float32Array(max * 4);
    this.size = new Float32Array(max);
    this.p = []; // live particles (CPU state)
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(this.pos, 3).setUsage(THREE.DynamicDrawUsage));
    geo.setAttribute('color', new THREE.BufferAttribute(this.col, 4).setUsage(THREE.DynamicDrawUsage));
    geo.setAttribute('size', new THREE.BufferAttribute(this.size, 1).setUsage(THREE.DynamicDrawUsage));
    geo.setDrawRange(0, 0);
    const mat = new THREE.ShaderMaterial({
      uniforms: { uScale: { value: 800 }, uSoft: { value: soft } },
      vertexShader: `attribute float size; attribute vec4 color; varying vec4 vC; uniform float uScale;
        void main(){ vC = color; vec4 mv = modelViewMatrix * vec4(position,1.0); gl_PointSize = size * uScale / max(1.0, -mv.z); gl_Position = projectionMatrix * mv; }`,
      fragmentShader: `varying vec4 vC; uniform float uSoft;
        void main(){ vec2 d = gl_PointCoord-0.5; float r = length(d)*2.0; if (r>1.0) discard;
          float a = pow(1.0-r, uSoft); gl_FragColor = vec4(vC.rgb, vC.a*a); }`,
      transparent: true, depthWrite: false, blending,
    });
    this.points = new THREE.Points(geo, mat);
    this.points.frustumCulled = false;
    this.points.renderOrder = blending === THREE.AdditiveBlending ? 3 : 2;
    scene.add(this.points);
  }
  spawn(o) { if (this.p.length < this.max) this.p.push(o); else this.p[(Math.random() * this.max) | 0] = o; }
  update(dt) {
    const keep = [];
    let n = 0;
    for (const q of this.p) {
      q.age += dt;
      if (q.age < 0) { keep.push(q); continue; }
      if (q.age > q.life) continue;
      const k = q.age / q.life;
      q.vy -= (q.g || 0) * dt;
      q.x += q.vx * dt; q.y += q.vy * dt; q.z += q.vz * dt;
      q.vx *= 1 - (q.drag || 0) * dt; q.vz *= 1 - (q.drag || 0) * dt; q.vy *= 1 - (q.drag || 0) * dt * 0.5;
      this.pos[n * 3] = q.x; this.pos[n * 3 + 1] = q.y; this.pos[n * 3 + 2] = q.z;
      const fade = k < 0.08 ? k / 0.08 : 1 - (k - 0.08) / 0.92;
      const c = q.c1 ? [q.c0[0] + (q.c1[0] - q.c0[0]) * k, q.c0[1] + (q.c1[1] - q.c0[1]) * k, q.c0[2] + (q.c1[2] - q.c0[2]) * k] : q.c0;
      this.col[n * 4] = c[0]; this.col[n * 4 + 1] = c[1]; this.col[n * 4 + 2] = c[2]; this.col[n * 4 + 3] = q.a * fade;
      this.size[n] = q.s0 + (q.s1 - q.s0) * k;
      n++;
      keep.push(q);
    }
    this.p = keep;
    const g = this.points.geometry;
    g.setDrawRange(0, n);
    g.attributes.position.needsUpdate = g.attributes.color.needsUpdate = g.attributes.size.needsUpdate = true;
  }
}

export class GroundFx {
  constructor(scene, quality = 1) {
    this.scene = scene;
    this.q = quality;
    this.glow = new ParticlePool(scene, 3000, THREE.AdditiveBlending, 1.6);
    this.smoke = new ParticlePool(scene, 4000, THREE.NormalBlending, 1.1);
    // tracer / beam segments
    this.maxSeg = 400;
    this.segPos = new Float32Array(this.maxSeg * 6);
    this.segCol = new Float32Array(this.maxSeg * 8);
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(this.segPos, 3).setUsage(THREE.DynamicDrawUsage));
    g.setAttribute('color', new THREE.BufferAttribute(this.segCol, 4).setUsage(THREE.DynamicDrawUsage));
    this.lines = new THREE.LineSegments(g, new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false }));
    this.lines.frustumCulled = false;
    scene.add(this.lines);
    this.segs = [];
    this.beamGeo = new THREE.CylinderGeometry(1, 1, 1, 6, 1, true).rotateX(Math.PI / 2); // unit beam along +Z
    this.beamPool = [];
    // flash lights (fixed count → no shader recompiles)
    this.lights = Array.from({ length: 4 }, () => { const l = new THREE.PointLight(0xffaa55, 0, 120, 2); scene.add(l); return { l, t: 0, life: 0, max: 0 }; });
    this.li = 0;
  }

  flash(p, intensity, life, color = 0xffa040, range = 120) {
    const s = this.lights[this.li++ % this.lights.length];
    s.l.position.copy(V(p)); s.l.position.y += 2;
    s.l.color.setHex(color); s.l.distance = range;
    s.max = intensity; s.life = life; s.t = 0;
  }

  seg(a, b, color, life, { delay = 0, travel = 0, len = 0, flight = 0, width = 0.18 } = {}) {
    if (this.segs.length > this.maxSeg) this.segs.shift();
    const s = { a: V(a), b: V(b), c: color, life, age: -delay, travel, len, flight, width };
    // a real 3D glowing beam (WebGL lines are always 1 px)
    let m = this.beamPool.pop();
    if (!m) { m = new THREE.Mesh(this.beamGeo, new THREE.MeshBasicMaterial({ transparent: true, blending: THREE.AdditiveBlending, depthWrite: false, fog: false })); m.frustumCulled = false; this.scene.add(m); }
    m.material.color.setRGB(color[0] * 2, color[1] * 2, color[2] * 2);
    m.visible = false;
    s.mesh = m;
    this.segs.push(s);
  }

  update(dt) {
    this.glow.update(dt);
    this.smoke.update(dt);
    let n = 0;
    const keep = [];
    const tmpA = new THREE.Vector3(), tmpB = new THREE.Vector3();
    for (const s of this.segs) {
      s.age += dt;
      if (s.age > s.life) continue;
      keep.push(s);
      if (s.age < 0) continue;
      let a = s.a, b = s.b;
      if (s.travel) {
        const total = a.distanceTo(b) || 1;
        const f = Math.min(1, s.age / Math.max(0.05, s.flight));
        const f0 = Math.max(0, f - s.len / total);
        tmpA.copy(a).lerp(b, f0); tmpB.copy(a).lerp(b, f);
        a = tmpA; b = tmpB;
      }
      const k = s.travel ? 1 : 1 - s.age / s.life;
      const m = s.mesh, len = a.distanceTo(b);
      if (m && len > 0.01) {
        m.visible = true;
        m.position.copy(a).lerp(b, 0.5);
        m.lookAt(b);
        m.scale.set(s.width * (s.travel ? 1 : 0.4 + k), s.width * (s.travel ? 1 : 0.4 + k), len);
        m.material.opacity = Math.min(1, k * 1.2);
      }
      n++;
    }
    for (const s of this.segs) if (s.age > s.life && s.mesh) { s.mesh.visible = false; this.beamPool.push(s.mesh); s.mesh = null; }
    this.segs = keep;
    const g = this.lines.geometry;
    g.setDrawRange(0, n * 2);
    g.attributes.position.needsUpdate = g.attributes.color.needsUpdate = true;
    for (const s of this.lights) {
      if (s.life <= 0) continue;
      s.t += dt;
      const k = Math.max(0, 1 - s.t / s.life);
      s.l.intensity = s.max * k * k;
      if (k <= 0) s.life = 0;
    }
  }

  // ── composites ───────────────────────────────────────────────────────────
  P(p, o) { return { x: p[0], y: p[2], z: -p[1], vx: 0, vy: 0, vz: 0, age: 0, a: 1, ...o }; }

  muzzle(p, big = true) {
    this.glow.spawn(this.P(p, { life: 0.09, s0: big ? 4 : 1.6, s1: big ? 7 : 2.5, c0: [1, 0.85, 0.5], a: 1 }));
    this.flash(p, big ? 400 : 60, big ? 0.12 : 0.06, 0xffc070, big ? 90 : 30);
    if (big) for (let i = 0; i < 6 * this.q; i++) this.smoke.spawn(this.P(p, { life: 2 + Math.random(), s0: 1.5, s1: 7, c0: [0.62, 0.6, 0.57], a: 0.45, vx: (Math.random() - 0.5) * 3, vy: 1 + Math.random(), vz: (Math.random() - 0.5) * 3, drag: 0.8 }));
  }
  explosion(p, size = 1, delay = 0) {
    const q = this.q;
    this.flash(p, 3500 * size, 0.45, 0xff9040, 320 * size);
    this.glow.spawn(this.P(p, { age: -delay, life: 0.18, s0: 14 * size, s1: 22 * size, c0: [1, 0.95, 0.8], a: 1 })); // blinding core
    for (let i = 0; i < 26 * q * size; i++) {
      const a = Math.random() * 6.283, v = (4 + Math.random() * 12) * size;
      this.glow.spawn(this.P(p, { age: -delay, life: 0.8 + Math.random() * 0.8, s0: 4.5 * size, s1: 13 * size, c0: [1, 0.78, 0.35], c1: [0.85, 0.2, 0.04], a: 0.95,
        vx: Math.cos(a) * v, vy: 4 + Math.random() * 10 * size, vz: Math.sin(a) * v, drag: 2.0 }));
    }
    for (let i = 0; i < 16 * q; i++) { // ground dust ring
      const a = (i / 16) * 6.283, v = 14 + Math.random() * 6;
      this.smoke.spawn(this.P([p[0], p[1], p[2] + 0.5], { age: -delay, life: 2.5 + Math.random(), s0: 3 * size, s1: 12 * size, c0: [0.62, 0.55, 0.45], a: 0.55,
        vx: Math.cos(a) * v, vy: 0.6, vz: Math.sin(a) * v, drag: 1.6 }));
    }
    for (let i = 0; i < 14 * q * size; i++) {
      const a = Math.random() * 6.283, v = (2 + Math.random() * 4) * size;
      this.smoke.spawn(this.P(p, { age: -delay - 0.1, life: 5 + Math.random() * 5, s0: 5 * size, s1: 24 * size, c0: [0.2, 0.18, 0.16], c1: [0.5, 0.47, 0.43], a: 0.65,
        vx: Math.cos(a) * v, vy: 2 + Math.random() * 3, vz: Math.sin(a) * v, drag: 0.7 }));
    }
    for (let i = 0; i < 24 * q; i++) {
      const a = Math.random() * 6.283, v = 15 + Math.random() * 30;
      this.glow.spawn(this.P(p, { age: -delay, life: 0.8 + Math.random() * 0.8, s0: 0.5, s1: 0.2, c0: [1, 0.8, 0.4], a: 1, g: 22,
        vx: Math.cos(a) * v * 0.6, vy: 10 + Math.random() * 25, vz: Math.sin(a) * v * 0.6 }));
    }
  }
  dust(p, delay = 0, water = false, color = [0.72, 0.62, 0.46]) {
    const c = water ? [0.85, 0.9, 0.95] : color;
    for (let i = 0; i < 10 * this.q; i++) {
      const a = Math.random() * 6.283, v = 1 + Math.random() * 4;
      this.smoke.spawn(this.P(p, { age: -delay, life: 1.2 + Math.random() * 1.5, s0: 1.5, s1: 6, c0: c, a: water ? 0.8 : 0.6, g: water ? 9 : 0,
        vx: Math.cos(a) * v, vy: water ? 6 + Math.random() * 6 : 1.5 + Math.random() * 2, vz: Math.sin(a) * v, drag: 1.2 }));
    }
  }
  sparks(p, n = 8) {
    for (let i = 0; i < n; i++) {
      const a = Math.random() * 6.283, v = 8 + Math.random() * 14;
      this.glow.spawn(this.P(p, { life: 0.4 + Math.random() * 0.4, s0: 0.35, s1: 0.1, c0: [1, 0.9, 0.6], a: 1, g: 15, vx: Math.cos(a) * v, vy: 4 + Math.random() * 8, vz: Math.sin(a) * v }));
    }
  }
  intercept(p) { this.sparks(p, 14); this.glow.spawn(this.P(p, { life: 0.25, s0: 3, s1: 6, c0: [0.5, 1, 1], a: 1 })); this.flash(p, 300, 0.15, 0x80ffff, 80); }
  wreckSmoke(p) {
    this.smoke.spawn(this.P([p[0] + (Math.random() - 0.5) * 2, p[1] + (Math.random() - 0.5) * 2, p[2] + 2], { life: 7, s0: 3, s1: 16, c0: [0.12, 0.11, 0.1], c1: [0.35, 0.34, 0.33], a: 0.55, vx: 0.8, vy: 3.5, vz: 0.3, drag: 0.15 }));
    if (Math.random() < 0.4) this.glow.spawn(this.P([p[0], p[1], p[2] + 1.2], { life: 0.5, s0: 2, s1: 3, c0: [1, 0.5, 0.15], a: 0.8, vy: 2 }));
  }
  trail(p, missile) {
    if (missile) this.glow.spawn(this.P(p, { life: 0.12, s0: 1.4, s1: 0.6, c0: [1, 0.75, 0.4], a: 1 }));
    this.smoke.spawn(this.P(p, { life: missile ? 2.5 : 1.4, s0: 0.8, s1: 3.5, c0: [0.75, 0.74, 0.72], a: 0.5, vy: 0.4, drag: 0.4 }));
  }
  /** Vehicle dust / spray behind moving ground units. */
  wake(p, speed, ground) {
    if (ground === 'WATER') return this.smoke.spawn(this.P(p, { life: 1.4, s0: 1, s1: 4, c0: [0.9, 0.95, 1], a: 0.55, vy: 1.5, g: 3 }));
    const col = { SAND: [0.85, 0.72, 0.52], SNOW: [0.95, 0.97, 1], ICE: [0.9, 0.95, 1], SALT: [0.92, 0.9, 0.86], ROCK: [0.6, 0.57, 0.53], ROAD: [0.7, 0.62, 0.5], GRASS: [0.55, 0.5, 0.4] }[ground];
    if (!col) return;
    const big = ground === 'SAND' || ground === 'SNOW' || ground === 'ROAD';
    this.smoke.spawn(this.P(p, { life: big ? 3.5 : 2, s0: 2, s1: big ? 11 : 6, c0: col, a: Math.min(0.5, speed / 30), vx: (Math.random() - 0.5) * 2, vy: 0.8 + Math.random(), vz: (Math.random() - 0.5) * 2, drag: 0.6 }));
  }

  /** Server FIRE event. */
  fire(ev, from, team) {
    const to = ev.to;
    const tc = team === 'RED' ? [1, 0.55, 0.3] : [0.55, 0.85, 1];
    if (ev.fx === 'RAIL') {
      this.seg(from, to, [0.3, 0.9, 1], 0.45, { width: 0.9 });
      this.seg(from, to, [1, 1, 1], 0.18, { width: 0.25 });
      this.muzzle(from, true);
      this.flash(from, 900, 0.18, 0x80ffff, 120);
    } else if (ev.fx === 'BEAM') {
      this.seg(from, to, [1, 0.3, 0.95], 0.3, { width: 0.2 });
    } else if (ev.fx === 'TRACER') {
      const small = /pistol|smg|rifle-m4|rifle-ak|dmr|sniper|shotgun/.test(ev.weapon || '');
      const rifle = small || /rifle/.test(ev.weapon || ''), auto = rifle || /hmg|ac-/.test(ev.weapon || '');
      const n = small ? 1 : auto ? 3 : 1;
      for (let i = 0; i < n; i++) this.seg(from, to, tc, Math.max(0.1, ev.flight) + 0.05, { delay: i * (rifle ? 0.1 : 0.08), travel: 1, len: rifle ? 6 : auto ? 18 : 45, flight: ev.flight, width: small ? 0.035 : rifle ? 0.05 : auto ? 0.12 : 0.3 });
      if (rifle) this.flash(from, 30, 0.05, 0xffc070, 12);
      else this.muzzle(from, !auto);
    } else if (ev.fx === 'SHELL' || ev.fx === 'ROCKET') {
      this.muzzle(from, true);
      for (let i = 0; i < 10; i++) this.smoke.spawn(this.P(from, { life: 3, s0: 3, s1: 12, c0: [0.7, 0.68, 0.65], a: 0.6, vx: (Math.random() - 0.5) * 4, vy: 2 + Math.random() * 3, vz: (Math.random() - 0.5) * 4, drag: 0.6 }));
    } else if (ev.fx === 'MISSILE') {
      this.glow.spawn(this.P(from, { life: 0.25, s0: 3, s1: 5, c0: [1, 0.8, 0.5], a: 1 }));
      for (let i = 0; i < 6; i++) this.smoke.spawn(this.P(from, { life: 2.5, s0: 2, s1: 8, c0: [0.8, 0.78, 0.75], a: 0.6, vx: (Math.random() - 0.5) * 3, vy: 1, vz: (Math.random() - 0.5) * 3, drag: 0.8 }));
    }
  }
  /** Server IMPACT event. */
  impact(ev, water, dustColor) {
    const d = ev.delay || 0;
    if (ev.hit) {
      if (ev.effect === 'NO_PEN') { this.sparks(ev.at, 10); this.flash(ev.at, 150, 0.08, 0xffd080, 40); }
      else this.explosion(ev.at, ev.effect === 'DESTROYED' ? 2.2 : 1, d);
    } else if (ev.effect === 'BLAST') { this.explosion(ev.at, 1.6, d); this.dust(ev.at, d, water, dustColor); }
    else this.dust(ev.at, d, water, dustColor);
  }
}

export { V as toThree };
