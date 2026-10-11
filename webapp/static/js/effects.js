// Weapon effects (LS-4): tracers along the line of fire, muzzle flashes, fireball + smoke + debris
// on hits, dust on misses, railgun beams, lasers, missile/shell trails, wreck smoke.
const C = Cesium;

function sprite(draw, size = 64) {
  const c = document.createElement('canvas');
  c.width = c.height = size;
  draw(c.getContext('2d'), size);
  return c;
}
function radial(stops) {
  return sprite((g, s) => {
    const gr = g.createRadialGradient(s / 2, s / 2, 0, s / 2, s / 2, s / 2);
    for (const [o, col] of stops) gr.addColorStop(o, col);
    g.fillStyle = gr;
    g.fillRect(0, 0, s, s);
  });
}
const SPR = {};
function sprites() {
  if (SPR.flash) return SPR;
  SPR.flash = radial([[0, 'rgba(255,255,240,1)'], [0.25, 'rgba(255,230,140,.95)'], [0.6, 'rgba(255,140,30,.45)'], [1, 'rgba(255,90,0,0)']]);
  SPR.fire = radial([[0, 'rgba(255,250,210,1)'], [0.3, 'rgba(255,170,40,.95)'], [0.65, 'rgba(220,60,10,.6)'], [1, 'rgba(120,20,0,0)']]);
  SPR.smoke = sprite((g, s) => {
    for (let i = 0; i < 7; i++) {
      const x = s / 2 + (Math.random() - 0.5) * s * 0.35, y = s / 2 + (Math.random() - 0.5) * s * 0.35, r = s * (0.22 + Math.random() * 0.2);
      const gr = g.createRadialGradient(x, y, 0, x, y, r);
      gr.addColorStop(0, 'rgba(70,70,70,.55)'); gr.addColorStop(1, 'rgba(60,60,60,0)');
      g.fillStyle = gr; g.fillRect(0, 0, s, s);
    }
  });
  SPR.dust = radial([[0, 'rgba(196,170,120,.75)'], [0.5, 'rgba(170,145,100,.4)'], [1, 'rgba(150,130,90,0)']]);
  SPR.spark = radial([[0, 'rgba(255,255,255,1)'], [0.4, 'rgba(255,220,120,.9)'], [1, 'rgba(255,120,0,0)']]);
  SPR.cyan = radial([[0, 'rgba(230,255,255,1)'], [0.35, 'rgba(60,240,255,.9)'], [1, 'rgba(0,140,255,0)']]);
  SPR.splash = radial([[0, 'rgba(230,245,255,.9)'], [0.5, 'rgba(160,200,230,.5)'], [1, 'rgba(120,170,210,0)']]);
  return SPR;
}

export class Effects {
  constructor(g) {
    this.g = g;
    sprites();
    this.bb = g.scene.primitives.add(new C.BillboardCollection({ blendOption: C.BlendOption.TRANSLUCENT }));
    this.lines = g.scene.primitives.add(new C.PolylineCollection());
    this.active = [];
    this.max = 900;
    this.quality = 1;
    g.scene.preRender.addEventListener(() => this.update());
  }
  pos(p) { return this.g.frame.cart(p[0], p[1], p[2]); }
  now() { return performance.now() / 1000; }

  add(e) {
    if (this.active.length > this.max) { const old = this.active.shift(); old.kill && old.kill(); }
    e.t0 = this.now() + (e.delay || 0);
    this.active.push(e);
  }
  billboard(image, position, size, color = C.Color.WHITE) {
    return this.bb.add({ image, position, width: size, height: size, color, show: false, sizeInMeters: true });
  }
  update() {
    const t = this.now();
    const keep = [];
    for (const e of this.active) {
      const age = t - e.t0;
      if (age < 0) { keep.push(e); continue; }
      if (e.step(age) === false) e.kill && e.kill(); else keep.push(e);
    }
    this.active = keep;
  }

  // ── primitives ──────────────────────────────────────────────────────────
  sprite(image, p, { size = 6, grow = 4, life = 0.6, rise = 0, alpha = 1, delay = 0, vel = null, color = C.Color.WHITE } = {}) {
    const base = this.pos(p);
    const b = this.billboard(image, base, size, color);
    const up = C.Cartesian3.normalize(base, new C.Cartesian3());
    const tmp = new C.Cartesian3();
    this.add({
      delay,
      step: (age) => {
        if (age > life) return false;
        const k = age / life;
        b.show = true;
        const s = size * (1 + grow * k);
        b.width = b.height = s;
        b.color = color.withAlpha(alpha * (1 - k) * (k < 0.1 ? k / 0.1 : 1));
        if (rise || vel) {
          C.Cartesian3.multiplyByScalar(up, rise * age, tmp);
          const pp = C.Cartesian3.add(base, tmp, new C.Cartesian3());
          if (vel) {
            const [vx, vy, vz] = vel;
            const q = this.g.frame.cart(p[0] + vx * age, p[1] + vy * age, p[2] + vz * age - 4.9 * age * age);
            b.position = q;
          } else b.position = pp;
        }
      },
      kill: () => this.bb.remove(b),
    });
  }
  line(a, b, { color, width = 2, life = 0.3, glow = 0.25, delay = 0 }) {
    const pl = this.lines.add({ positions: [this.pos(a), this.pos(b)], width, show: false,
      material: C.Material.fromType('PolylineGlow', { color, glowPower: glow, taperPower: 1 }) });
    this.add({ delay, step: (age) => { if (age > life) return false; pl.show = true; pl.material.uniforms.color = color.withAlpha(1 - age / life); }, kill: () => this.lines.remove(pl) });
  }
  /** Moving tracer segment from a to b over `flight` seconds. */
  tracer(a, b, flight, color, { len = 45, width = 3, delay = 0 } = {}) {
    const d = Math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2]) || 1;
    const T = Math.max(0.08, flight);
    const pl = this.lines.add({ positions: [this.pos(a), this.pos(a)], width, show: false,
      material: C.Material.fromType('PolylineGlow', { color, glowPower: 0.3, taperPower: 0.6 }) });
    const at = (f) => [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f];
    this.add({ delay, step: (age) => {
      if (age > T) return false;
      const f = age / T, f0 = Math.max(0, f - len / d);
      pl.show = true;
      pl.positions = [this.pos(at(f0)), this.pos(at(f))];
    }, kill: () => this.lines.remove(pl) });
  }

  // ── composite effects ──────────────────────────────────────────────────
  muzzle(p, big = false, delay = 0) {
    this.sprite(SPR.flash, p, { size: big ? 9 : 4, grow: 1.4, life: big ? 0.16 : 0.09, delay });
    if (big && this.quality > 0.5) this.sprite(SPR.smoke, p, { size: 6, grow: 3, life: 2.2, rise: 2, alpha: 0.55, delay });
  }
  explosion(p, size = 1, delay = 0) {
    const q = this.quality;
    this.sprite(SPR.flash, [p[0], p[1], p[2] + 2 * size], { size: 10 * size, grow: 2.2, life: 0.22, delay });
    this.sprite(SPR.fire, [p[0], p[1], p[2] + 3 * size], { size: 9 * size, grow: 2.6, life: 0.9, rise: 4, delay });
    for (let i = 0; i < 3 * q; i++) this.sprite(SPR.smoke, [p[0] + (Math.random() - 0.5) * 6, p[1] + (Math.random() - 0.5) * 6, p[2] + 4], { size: 10 * size, grow: 3.5, life: 4 + Math.random() * 2, rise: 5, alpha: 0.75, delay: delay + 0.15 });
    for (let i = 0; i < 10 * q; i++) {
      const a = Math.random() * Math.PI * 2, v = 12 + Math.random() * 25;
      this.sprite(SPR.spark, [p[0], p[1], p[2] + 2], { size: 1.6, grow: 0, life: 0.9 + Math.random() * 0.6, vel: [Math.cos(a) * v, Math.sin(a) * v, 10 + Math.random() * 20], delay });
    }
  }
  dust(p, delay = 0, water = false) {
    this.sprite(water ? SPR.splash : SPR.dust, [p[0], p[1], p[2] + 1.5], { size: 5, grow: 3, life: 1.6, rise: 2.5, alpha: 0.85, delay });
    this.sprite(water ? SPR.splash : SPR.dust, [p[0], p[1], p[2] + 3], { size: 3, grow: 2, life: 0.9, rise: 6, alpha: 0.7, delay: delay + 0.05 });
  }
  intercept(p) {
    this.sprite(SPR.cyan, p, { size: 6, grow: 2, life: 0.35 });
    for (let i = 0; i < 6; i++) { const a = Math.random() * 6.28; this.sprite(SPR.spark, p, { size: 1.2, grow: 0, life: 0.6, vel: [Math.cos(a) * 20, Math.sin(a) * 20, 8] }); }
  }
  smokeColumn(p, life = 6) {
    this.sprite(SPR.smoke, [p[0] + (Math.random() - 0.5) * 3, p[1] + (Math.random() - 0.5) * 3, p[2] + 3], { size: 7, grow: 4, life, rise: 6, alpha: 0.6 });
    if (Math.random() < 0.3) this.sprite(SPR.fire, [p[0], p[1], p[2] + 1.5], { size: 3, grow: 1, life: 0.6, rise: 2, alpha: 0.8 });
  }
  trail(p, color = 'smoke') {
    this.sprite(color === 'fire' ? SPR.fire : SPR.smoke, p, { size: color === 'fire' ? 2.2 : 3, grow: color === 'fire' ? 0.5 : 3, life: color === 'fire' ? 0.25 : 2.5, rise: 0.5, alpha: 0.55 });
  }

  /** A FIRE event from the server. */
  fire(ev, shooterPos, teamColor) {
    const from = shooterPos || ev.from;
    const fx = ev.fx;
    if (fx === 'RAIL') {
      this.line(from, ev.to, { color: C.Color.fromCssColorString('#7ff6ff'), width: 6, life: 0.45, glow: 0.35 });
      this.line(from, ev.to, { color: C.Color.WHITE, width: 2, life: 0.18, glow: 0.1 });
      this.muzzle(from, true);
    } else if (fx === 'BEAM') {
      this.line(from, ev.to, { color: C.Color.fromCssColorString('#ff3df2'), width: 3, life: 0.35, glow: 0.4 });
    } else if (fx === 'TRACER') {
      const n = ev.weapon && /hmg|ac-/.test(ev.weapon) ? 3 : 1;
      for (let i = 0; i < n; i++) this.tracer(from, ev.to, ev.flight, teamColor, { len: n > 1 ? 25 : 60, width: n > 1 ? 2 : 4, delay: i * 0.09 });
      this.muzzle(from, n === 1);
    } else if (fx === 'SHELL' || fx === 'ROCKET') {
      this.muzzle(from, true);
      if (fx === 'ROCKET') this.sprite(SPR.smoke, from, { size: 10, grow: 4, life: 3, rise: 3, alpha: 0.7 });
    } else if (fx === 'MISSILE') {
      this.sprite(SPR.flash, from, { size: 5, grow: 1, life: 0.2 });
      this.sprite(SPR.smoke, from, { size: 6, grow: 3, life: 2.5, rise: 1, alpha: 0.6 });
    }
  }
  /** An IMPACT event from the server. */
  impact(ev, isWater) {
    const delay = ev.delay || 0;
    if (ev.hit) {
      const big = ev.effect === 'DESTROYED' ? 2.2 : ev.effect === 'NO_PEN' ? 0.45 : 1;
      if (ev.effect === 'NO_PEN') this.sprite(SPR.spark, ev.at, { size: 3, grow: 1.5, life: 0.25, delay });
      else this.explosion(ev.at, big, delay);
    } else if (ev.effect === 'BLAST') {
      this.explosion(ev.at, 1.6, delay);
      this.dust(ev.at, delay, isWater);
    } else this.dust(ev.at, delay, isWater);
  }
}
