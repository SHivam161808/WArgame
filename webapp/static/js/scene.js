// BattleScene: renders TickFrames on the globe — models, bars, labels, HVT markers, projectiles,
// effects, routes — interpolating between ticks for smooth motion (DC-4, LS-2, LS-3, LS-6, VZ-1).
import { Effects } from './effects.js';
import { Environment } from './environment.js';
import { angleDiff, lerp, lerpAngle } from './geo.js';

const C = Cesium;
const DEG = Math.PI / 180;
const AIR = new Set(['GUNSHIP', 'JET', 'UAV']);
export const TEAM = { BLUE: C.Color.fromCssColorString('#36b8ff'), RED: C.Color.fromCssColorString('#ff4b3e') };
const TRACER = { BLUE: C.Color.fromCssColorString('#7fd4ff'), RED: C.Color.fromCssColorString('#ff9a5a') };

function canvas(w, h, draw) { const c = document.createElement('canvas'); c.width = w; c.height = h; draw(c.getContext('2d'), w, h); return c; }
const barCache = new Map();
function barImage(side, hp, morale, flags) {
  const key = `${side}|${hp}|${morale}|${flags}`;
  if (barCache.has(key)) return barCache.get(key);
  const img = canvas(76, flags ? 26 : 14, (g, w) => {
    g.fillStyle = 'rgba(0,0,0,.65)'; g.fillRect(0, 0, w, 14);
    const hc = hp > 60 ? '#4be38a' : hp > 30 ? '#ffb547' : '#ff4b3e';
    g.fillStyle = hc; g.fillRect(2, 2, (w - 4) * hp / 100, 5);
    g.fillStyle = '#9db7ff'; g.fillRect(2, 9, (w - 4) * morale / 100, 3);
    g.strokeStyle = side === 'BLUE' ? '#36b8ff' : '#ff4b3e'; g.lineWidth = 1.5; g.strokeRect(0.75, 0.75, w - 1.5, 12.5);
    if (flags) {
      g.font = 'bold 11px Rajdhani, sans-serif'; g.textAlign = 'center';
      g.fillStyle = '#ff4b3e'; g.fillText(flags, w / 2, 25);
    }
  });
  barCache.set(key, img);
  return img;
}
function hvtIcon(side, destroyed) {
  const col = side === 'BLUE' ? '#36b8ff' : '#ff4b3e';
  return canvas(64, 64, (g) => {
    g.translate(32, 32);
    if (destroyed) {
      g.strokeStyle = '#111'; g.lineWidth = 12; g.beginPath(); g.moveTo(-18, -18); g.lineTo(18, 18); g.moveTo(18, -18); g.lineTo(-18, 18); g.stroke();
      g.strokeStyle = col; g.lineWidth = 6; g.stroke();
      return;
    }
    g.rotate(Math.PI / 4);
    g.fillStyle = 'rgba(0,0,0,.6)'; g.fillRect(-19, -19, 38, 38);
    g.strokeStyle = col; g.lineWidth = 4; g.strokeRect(-17, -17, 34, 34);
    g.rotate(-Math.PI / 4);
    g.fillStyle = col; g.font = 'bold 16px Rajdhani, sans-serif'; g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText('HVT', 0, 1);
  });
}
const shellDot = canvas(16, 16, (g) => { const r = g.createRadialGradient(8, 8, 0, 8, 8, 8); r.addColorStop(0, '#fff'); r.addColorStop(0.4, '#ffd27a'); r.addColorStop(1, 'rgba(255,120,0,0)'); g.fillStyle = r; g.fillRect(0, 0, 16, 16); });

// civilian vehicles are drawn procedurally in the 3D view; the map uses the closest stock model
const MAP_MODEL = { car: 'recon', jeep: 'recon', bike: 'recon' };

export class BattleScene {
  constructor(g, session, { side = null, onSelect = null } = {}) {
    this.g = g;
    this.session = session;
    this.cat = session.catalogue;
    this.mySide = side;
    this.onSelect = onSelect;
    this.units = new Map();
    this.projectiles = new Map();
    this.routes = new Map();
    this.prev = null;
    this.cur = null;
    this.tCur = 0;
    this.interval = (session.scenario?.cadence_ms || 250) / 1000;
    this.effects = new Effects(g);
    this.env = new Environment(g, session.theatre);
    if ((g.debug || '').includes('noenv')) for (const k in this.env.stage) { this.env.stage[k].enabled = false; this.env.stage[k].show = false; }
    this.env.setWeather(session.scenario?.weather || 'CLEAR');
    this.env.setHour(hourOf(session.scenario?.time_of_day));
    this.bb = g.scene.primitives.add(new C.BillboardCollection());
    this.labels = g.scene.primitives.add(new C.LabelCollection());
    this.groundQueue = 0;
    this.camMode = 'free';
    this.camTarget = null;
    this.followRange = 60;
    this.viewIdx = 0;
    this.rangeMul = 1;
    this.aim = { yaw: 0, pitch: 0 };
    this.zoom = 1;
    this.sensor = 'DAY';
    this.showLabels = true;
    this.waterLevel = session.terrain?.meta?.water_level ?? null;
    this.scratch = { m: new C.Matrix4(), hpr: new C.HeadingPitchRoll(), r: new C.Matrix3(), t: new C.Matrix4() };
    g.scene.preRender.addEventListener(() => this.render());
  }

  spec(pid) { return this.cat.platforms[pid] || {}; }

  // ── frames ─────────────────────────────────────────────────────────────
  /** opts.instant: jump (replay seek) — no interpolation, no effects. */
  ingest(frame, opts = {}) {
    if ((this.g.debug || '').includes('nounits')) return;
    const now = performance.now() / 1000;
    if (this.cur && !opts.instant) {
      const dt = now - this.tCur;
      if (dt > 0.01 && dt < 5) this.interval = this.interval * 0.8 + dt * 0.2;
    }
    this.prev = opts.instant || !this.cur ? frame : this.cur;
    this.cur = frame;
    this.tCur = now;
    const seen = new Set();
    for (const u of frame.units) {
      seen.add(u.id);
      let vis = this.units.get(u.id);
      if (!vis) vis = this.createUnit(u);
      vis.a = opts.instant || !vis.b ? u : vis.b;
      vis.b = u;
      this.updateStatus(vis, u);
    }
    for (const [id, vis] of this.units) if (!seen.has(id)) { this.removeUnit(vis); this.units.delete(id); }
    this.syncProjectiles(frame, opts.instant);
    if (!opts.instant && opts.effects !== false) this.playEvents(frame.events || []);
    this.syncRoutes(frame);
    if (frame.hour != null && this.session.scenario?.time_of_day === 'DYNAMIC') this.env.setHour(frame.hour);
  }

  reset() {
    for (const vis of this.units.values()) this.removeUnit(vis);
    this.units.clear();
    for (const p of this.projectiles.values()) this.removeProjectile(p);
    this.projectiles.clear();
    this.prev = this.cur = null;
  }

  createUnit(u) {
    const spec = this.spec(u.p);
    const air = AIR.has(u.c);
    const vis = { id: u.id, side: u.s, cls: u.c, spec, air, model: null, nodes: {}, a: null, b: null, dead: false, smokeT: 0 };
    const team = u.s === 'BLUE' ? 'blue' : 'red';
    const dbg = this.g.debug || '';
    if (!dbg.includes('nomodels')) C.Model.fromGltfAsync({
      url: u.c === 'INF' ? '/static/characters/swat.glb' : `/static/models/${MAP_MODEL[spec.model] || spec.model || 'mbt'}_${team}.glb`, id: u.id, scene: this.g.scene,
      minimumPixelSize: u.hvt ? 56 : air ? 40 : u.c === 'INF' ? 22 : 34, maximumScale: 80, shadows: C.ShadowMode.DISABLED,
      environmentMapOptions: { enabled: false }, // per-model dynamic cube maps cost ~20 ms each
      colorBlendMode: C.ColorBlendMode.MIX, colorBlendAmount: 0, silhouetteColor: C.Color.YELLOW, silhouetteSize: 0,
    }).then((m) => {
      if (vis.removed) { m.destroy(); return; }
      vis.model = this.g.scene.primitives.add(m);
      m.readyEvent.addEventListener(() => {
        for (const n of ['turret', 'gun', 'rotor_l', 'rotor_r', 'prop']) {
          try { const node = m.getNode(n); if (node) vis.nodes[n] = node; } catch (_) { /* node absent */ }
        }
        this.applyLook(vis);
      });
    }).catch((e) => console.warn('model', spec.model, e));
    const height = (spec.dims?.height || 2.5) + (u.hvt ? 10 : 4);
    vis.bar = this.bb.add({ image: barImage(u.s, 100, 100, ''), position: C.Cartesian3.ZERO, verticalOrigin: C.VerticalOrigin.BOTTOM,
      pixelOffset: new C.Cartesian2(0, -6),
      scaleByDistance: new C.NearFarScalar(500, 1.0, 20000, 0.55), translucencyByDistance: new C.NearFarScalar(14000, 1, 30000, 0), disableDepthTestDistance: 3000 });
    vis.label = this.labels.add({ text: u.n, font: '600 13px Rajdhani, sans-serif', fillColor: TEAM[u.s], outlineColor: C.Color.BLACK, outlineWidth: 3,
      style: C.LabelStyle.FILL_AND_OUTLINE, verticalOrigin: C.VerticalOrigin.BOTTOM, pixelOffset: new C.Cartesian2(0, -24),
      scaleByDistance: new C.NearFarScalar(500, 1.0, 20000, 0.6), translucencyByDistance: new C.NearFarScalar(9000, 1, 20000, 0), disableDepthTestDistance: 3000 });
    vis.height = height;
    if (u.hvt) {
      vis.icon = this.bb.add({ image: hvtIcon(u.s, false), position: C.Cartesian3.ZERO, verticalOrigin: C.VerticalOrigin.BOTTOM,
        pixelOffset: new C.Cartesian2(0, -40), width: 46, height: 46,
        disableDepthTestDistance: Number.POSITIVE_INFINITY, scaleByDistance: new C.NearFarScalar(1000, 1.1, 60000, 0.7) });
    }
    this.units.set(u.id, vis);
    return vis;
  }

  removeUnit(vis) {
    vis.removed = true;
    if (vis.model) this.g.scene.primitives.remove(vis.model);
    this.bb.remove(vis.bar);
    this.labels.remove(vis.label);
    if (vis.icon) this.bb.remove(vis.icon);
  }

  updateStatus(vis, u) {
    const hp = Math.round((u.hp / u.mhp) * 20) * 5;
    const m = Math.round((u.m ?? 100) / 10) * 10;
    const flags = u.st === 'DESTROYED' ? 'DESTROYED' : u.mk ? 'IMMOBILISED' : u.fk ? 'WEAPONS OUT' : u.sw ? 'SWIMMING' : '';
    const key = `${hp}|${m}|${flags}`;
    if (vis.barKey !== key) { vis.bar.image = barImage(u.s, hp, m, flags === 'SWIMMING' ? '≈ SWIM' : flags); vis.barKey = key; }
    const dead = u.st === 'DESTROYED';
    if (dead !== vis.dead) { vis.dead = dead; this.applyLook(vis); if (vis.icon) vis.icon.image = hvtIcon(u.s, dead); }
    vis.label.text = u.ctl && u.ctl !== 'AI' && this.mySide === u.s ? `${u.n} ◉` : u.n;
  }

  applyLook(vis) {
    const m = vis.model;
    if (!m) return;
    if (vis.dead) { m.color = C.Color.fromBytes(22, 20, 18); m.colorBlendAmount = 0.82; }
    else if (this.sensor === 'THERMAL') { m.color = C.Color.WHITE; m.colorBlendAmount = 0.92; }
    else { m.color = C.Color.WHITE; m.colorBlendAmount = 0; }
    m.silhouetteSize = this.selected === vis.id ? 2.5 : 0;
  }

  // ── projectiles ───────────────────────────────────────────────────────
  syncProjectiles(frame, instant) {
    const seen = new Set();
    for (const p of frame.projectiles || []) {
      seen.add(p.id);
      let v = this.projectiles.get(p.id);
      if (!v) {
        v = { id: p.id, kind: p.k, a: p, b: p, lastTrail: 0 };
        if (p.k === 'MISSILE') {
          C.Model.fromGltfAsync({ url: '/static/models/missile.glb', minimumPixelSize: 10, maximumScale: 40, environmentMapOptions: { enabled: false } }).then((m) => {
            if (v.removed) { m.destroy(); return; }
            v.model = this.g.scene.primitives.add(m);
          }).catch(() => {});
        }
        v.dot = this.bb.add({ image: shellDot, position: this.g.frame.cart(p.x, p.y, p.z), width: p.k === 'MISSILE' ? 10 : 12, height: p.k === 'MISSILE' ? 10 : 12, disableDepthTestDistance: 0 });
        this.projectiles.set(p.id, v);
      }
      v.a = instant ? p : v.b;
      v.b = p;
    }
    for (const [id, v] of this.projectiles) if (!seen.has(id)) { this.removeProjectile(v); this.projectiles.delete(id); }
  }
  removeProjectile(v) {
    v.removed = true;
    if (v.model) this.g.scene.primitives.remove(v.model);
    if (v.dot) this.bb.remove(v.dot);
  }

  playEvents(events) {
    for (const ev of events) {
      if (ev.t === 'FIRE') {
        const sv = this.units.get(ev.shooter);
        let from = ev.from;
        if (sv && sv.pos) from = [sv.pos[0], sv.pos[1], ev.from[2]];
        this.effects.fire(ev, from, TRACER[sv?.side || 'BLUE']);
      } else if (ev.t === 'IMPACT') {
        const water = this.waterLevel != null && ev.at[2] <= this.waterLevel + 0.5;
        this.effects.impact(ev, water);
      } else if (ev.t === 'INTERCEPT') this.effects.intercept(ev.at);
    }
  }

  // ── routes (VZ-2) ──────────────────────────────────────────────────────
  // Plain polylines a few metres above the rendered terrain: cheap to rebuild when AI
  // formations re-plan (ground-clamped entity polylines rebatch everything on every change).
  syncRoutes(frame) {
    if (!this.routeLines) this.routeLines = this.g.scene.primitives.add(new C.PolylineCollection());
    const want = new Map();
    for (const f of frame.formations || []) {
      if (!f.rt || !f.rt.length) continue;
      const lead = f.u.map((id) => frame.units.find((u) => u.id === id)).find((u) => u && u.st !== 'DESTROYED' && !AIR.has(u.c));
      if (!lead) continue;
      want.set(`F:${f.s}:${f.id}`, { side: f.s, pts: [[lead.x, lead.y, lead.z], ...f.rt], key: JSON.stringify(f.rt) });
    }
    for (const u of frame.units) if (u.rt && u.rt.length && u.st !== 'DESTROYED') want.set(`U:${u.id}`, { side: u.s, pts: [[u.x, u.y, u.z], ...u.rt], key: JSON.stringify(u.rt), unit: true });
    for (const [k, r] of this.routes) if (!want.has(k) || want.get(k).key !== r.key) { this.routeLines.remove(r.line); this.routes.delete(k); }
    const globe = this.g.scene.globe;
    for (const [k, r] of want) {
      if (this.routes.has(k)) continue;
      const pos = [];
      let last = r.pts[0];
      for (const p of r.pts) {
        // densify long legs so the trace follows the ground
        const n = Math.max(1, Math.ceil(Math.hypot(p[0] - last[0], p[1] - last[1]) / 400));
        for (let i = pos.length ? 1 : 0; i <= n; i++) {
          const x = last[0] + ((p[0] - last[0]) * i) / n, y = last[1] + ((p[1] - last[1]) * i) / n;
          const h = globe.getHeight(C.Cartographic.fromCartesian(this.g.frame.cart(x, y))) ?? p[2] ?? last[2] ?? 0;
          pos.push(this.g.frame.cart(x, y, h + 12));
        }
        last = p;
      }
      r.line = this.routeLines.add({ positions: pos, width: r.unit ? 3 : 5,
        material: C.Material.fromType('PolylineArrow', { color: TEAM[r.side].withAlpha(r.unit ? 0.65 : 0.9) }) });
      this.routes.set(k, r);
    }
  }

  // ── per-frame render ──────────────────────────────────────────────────
  alpha() {
    const t = performance.now() / 1000 - this.tCur;
    return Math.min(1.15, t / Math.max(0.03, this.interval));
  }
  interp(vis) {
    const a = vis.a, b = vis.b;
    if (!a || !b) return null;
    const k = this.paused ? 1 : Math.min(1, this.alpha());
    return {
      x: lerp(a.x, b.x, k), y: lerp(a.y, b.y, k), z: lerp(a.z, b.z, k), h: lerpAngle(a.h, b.h, k), pi: lerp(a.pi, b.pi, k), ro: lerp(a.ro, b.ro, k),
      ty: lerpAngle(a.ty, b.ty, k), gp: lerp(a.gp, b.gp, k), v: b.v,
    };
  }

  /** Height of the *rendered* terrain under a unit (cached; refreshed a few units per frame). */
  groundZ(vis, x, y, fallback) {
    const now = performance.now();
    const moved = vis.gx === undefined || Math.abs(vis.gx - x) + Math.abs(vis.gy - y) > 6;
    if ((moved || now - (vis.gt || 0) > 1500) && this.groundBudget > 0) {
      this.groundBudget--;
      const h = this.g.scene.globe.getHeight(C.Cartographic.fromCartesian(this.g.frame.cart(x, y)));
      if (h !== undefined) { vis.gh = h; vis.gx = x; vis.gy = y; vis.gt = now; }
    }
    if (vis.gh === undefined) return fallback;
    // follow the server slope between samples so moving vehicles stay glued to the ground
    return vis.gh + (fallback - (vis.gzs ?? fallback));
  }

  render() {
    if ((this.g.debug || '').includes('norender')) return;
    const { frame } = this.g;
    const S = this.scratch;
    const spin = performance.now() / 1000;
    this.groundBudget = 14;
    this.frameNo = (this.frameNo || 0) + 1;
    for (const vis of this.units.values()) {
      const s = this.interp(vis);
      if (!s) continue;
      const before = vis.gt;
      const gz = vis.air ? s.z : this.groundZ(vis, s.x, s.y, s.z);
      if (vis.gt !== before) vis.gzs = s.z;
      vis.pos = [s.x, s.y, gz];
      vis.heading = s.h;
      const hoverLift = vis.spec.hover ? 0.6 : 0;
      const p = frame.cart(s.x, s.y, vis.air ? s.z : gz + hoverLift);
      if (vis.model && vis.cls === 'INF') { const show = !vis.b?.mt; if (vis.model.show !== show && vis !== this.hiddenOwn) vis.model.show = show; }
      if (vis.model && !this.debugFreeze) {
        // Only touch Cesium when something visibly changed: every modelMatrix / node.matrix write
        // forces Cesium to rebuild the model's draw-command transforms.
        const pitch = vis.air ? s.pi : vis.dead ? s.pi + 2 : s.pi;
        const roll = vis.air ? 0 : vis.dead ? 6 : s.ro;
        const key = `${p.x.toFixed(2)}|${p.y.toFixed(2)}|${p.z.toFixed(2)}|${s.h.toFixed(1)}|${pitch.toFixed(1)}|${roll.toFixed(1)}`;
        if (key !== vis.mKey && !this.dbgNoMatrix) {
          vis.mKey = key;
          S.hpr.heading = (s.h - 90) * DEG; S.hpr.pitch = pitch * DEG; S.hpr.roll = roll * DEG;
          // Model.modelMatrix keeps a reference: each model needs its own matrix object
          vis.model.modelMatrix = C.Transforms.headingPitchRollToFixedFrame(p, S.hpr, C.Ellipsoid.WGS84, undefined, vis.mat || (vis.mat = new C.Matrix4()));
        }
        const n = this.dbgNoNodes ? {} : vis.nodes;
        const rel = Math.round(angleDiff(s.h, s.ty) * 2) / 2, gp = Math.round((s.gp || 0) * 2) / 2;
        if (n.turret && rel !== vis.tRel) {
          vis.tRel = rel;
          C.Matrix3.fromRotationY(-rel * DEG, S.r);
          n.turret.matrix = C.Matrix4.multiply(n.turret.originalMatrix, C.Matrix4.fromRotation(S.r, S.t), new C.Matrix4());
        }
        if (n.gun && gp !== vis.tGp) {
          vis.tGp = gp;
          C.Matrix3.fromRotationX(-gp * DEG, S.r);
          n.gun.matrix = C.Matrix4.multiply(n.gun.originalMatrix, C.Matrix4.fromRotation(S.r, S.t), new C.Matrix4());
        }
        if ((n.rotor_l || n.prop) && !vis.dead && (this.frameNo % 3 === 0)) {
          for (const r of ['rotor_l', 'rotor_r']) if (n[r]) { C.Matrix3.fromRotationY(spin * 25 * (r === 'rotor_l' ? 1 : -1), S.r); n[r].matrix = C.Matrix4.multiply(n[r].originalMatrix, C.Matrix4.fromRotation(S.r, S.t), new C.Matrix4()); }
          if (n.prop) { C.Matrix3.fromRotationZ(spin * 40, S.r); n.prop.matrix = C.Matrix4.multiply(n.prop.originalMatrix, C.Matrix4.fromRotation(S.r, S.t), new C.Matrix4()); }
        }
      }
      const topZ = vis.air ? s.z + 8 : gz + vis.height;
      const tKey = `${s.x.toFixed(1)}|${s.y.toFixed(1)}|${topZ.toFixed(1)}`;
      if (tKey !== vis.tKey) { // label/billboard writes rebuild glyph buffers — skip when static
        vis.tKey = tKey;
        const top = frame.cart(s.x, s.y, topZ);
        vis.bar.position = top;
        vis.label.position = top;
        if (vis.icon) vis.icon.position = frame.cart(s.x, s.y, topZ + 6);
      }
      if (vis.label.show !== this.showLabels) vis.label.show = this.showLabels;
      if (vis.dead && vis.b.st === 'DESTROYED') {
        const t = performance.now();
        if (t - vis.smokeT > 650 && this.camMode !== 'scope') { vis.smokeT = t; this.effects.smokeColumn([s.x, s.y, gz], 5); }
      }
    }
    for (const v of this.projectiles.values()) {
      const k = Math.min(1, this.alpha());
      const x = lerp(v.a.x, v.b.x, k), y = lerp(v.a.y, v.b.y, k), z = lerp(v.a.z, v.b.z, k);
      const p = frame.cart(x, y, z);
      v.dot.position = p;
      if (v.model) {
        const dx = v.b.x - v.a.x, dy = v.b.y - v.a.y, dz = v.b.z - v.a.z;
        const hd = Math.atan2(dx, dy) / DEG;
        S.hpr.heading = (hd - 90) * DEG; S.hpr.pitch = Math.atan2(dz, Math.hypot(dx, dy) || 1); S.hpr.roll = 0;
        v.model.modelMatrix = C.Transforms.headingPitchRollToFixedFrame(p, S.hpr, C.Ellipsoid.WGS84, undefined, v.mat || (v.mat = new C.Matrix4()));
      }
      const t = performance.now();
      if (t - v.lastTrail > (v.kind === 'MISSILE' ? 45 : 90)) { v.lastTrail = t; this.effects.trail([x, y, z], 'smoke'); }
    }
    this.updateCamera();
  }

  // ── selection & cameras ───────────────────────────────────────────────
  pickUnit(screen) {
    const picked = this.g.scene.pick(screen);
    if (picked && typeof picked.id === 'string' && this.units.has(picked.id)) return picked.id;
    return null;
  }
  select(id) {
    const old = this.selected;
    this.selected = id;
    for (const k of [old, id]) { const v = this.units.get(k); if (v) this.applyLook(v); }
    this.onSelect && this.onSelect(id);
  }
  view(id) { return this.cur?.units.find((u) => u.id === id) || null; }
  rendered(id) { return this.units.get(id)?.pos || null; }

  setSensor(mode) {
    this.sensor = mode;
    this.env.setSensor(mode);
    for (const v of this.units.values()) this.applyLook(v);
  }

  freeCam() {
    if (this.hiddenOwn?.model) this.hiddenOwn.model.show = true;
    this.hiddenOwn = null;
    this.camMode = 'free';
    this.camTarget = null;
    const cam = this.g.scene.camera;
    cam.lookAtTransform(C.Matrix4.IDENTITY);
    cam.frustum.fov = C.Math.toRadians(60);
    const ctl = this.g.scene.screenSpaceCameraController;
    ctl.enableInputs = true;
    this.setSensor('DAY');
  }
  follow(id) {
    this.camMode = 'follow';
    this.camTarget = id;
    const v = this.units.get(id);
    this.followRange = Math.max(35, (v?.spec?.dims?.length || 8) * 5);
    this.followPitch = -14;
    this.followHeadingOffset = 0;
    this.camSmooth = null;
  }
  drive(id) { this.camMode = 'drive'; this.camTarget = id; this.camSmooth = null; }
  scope(id) { this.camMode = 'scope'; this.camTarget = id; }

  /** Cycle camera view (C key) while driving or spectating. */
  cycleView(dir = 1) {
    this.viewIdx = (this.viewIdx + dir + CAM_VIEWS.length) % CAM_VIEWS.length;
    this.camSmooth = null; // snap to the new view
    return CAM_VIEWS[this.viewIdx];
  }
  get viewLabel() { return CAM_VIEWS[this.viewIdx].label; }

  updateCamera() {
    if (this.camMode === 'free' || !this.camTarget) return;
    const vis = this.units.get(this.camTarget);
    if (!vis || !vis.pos) return;
    const cam = this.g.scene.camera;
    const [x, y, z] = vis.pos;
    const dims = vis.spec.dims || { length: 8, width: 3.5, height: 2.4 };
    const eyeH = dims.height * 0.95 + (vis.cls === 'RECON' ? 1.5 : 0);
    const now = performance.now() / 1000;
    const dt = Math.min(0.1, now - (this.camT || now));
    this.camT = now;
    const ground = vis.air ? z : z;
    const vw = this.camMode === 'scope' ? 'scope' : CAM_VIEWS[this.viewIdx].id;
    const hull = vis.heading;
    const aimYaw = this.camMode === 'drive' ? this.aim.yaw : hull;
    const aimPitch = this.camMode === 'drive' ? this.aim.pitch : 0;
    // exponential smoothing of the camera yaw/pitch removes tick-to-tick jitter
    const k = 1 - Math.exp(-dt * 9);
    const wantYaw = vw === 'hull' || vw === 'driver' || vw === 'top' ? hull : vw === 'orbit' ? (now * 12) % 360 : aimYaw;
    if (!this.camSmooth) this.camSmooth = { yaw: wantYaw, pitch: aimPitch };
    let dy = ((wantYaw - this.camSmooth.yaw + 540) % 360) - 180;
    this.camSmooth.yaw = (this.camSmooth.yaw + dy * (vw === 'orbit' ? 1 : k) + 360) % 360;
    this.camSmooth.pitch += (aimPitch - this.camSmooth.pitch) * k;
    const yaw = this.camSmooth.yaw, pitch = this.camSmooth.pitch;

    // first-person views would clip through the own hull / barrel — hide the own model there
    const hideOwn = vw === 'scope' || vw === 'driver';
    if (vis.model && vis.model.show === hideOwn) vis.model.show = !hideOwn;
    if (this.hiddenOwn && this.hiddenOwn !== vis && this.hiddenOwn.model) this.hiddenOwn.model.show = true;
    this.hiddenOwn = hideOwn ? vis : null;
    if (vw === 'scope' || vw === 'commander' || vw === 'driver') {
      cam.lookAtTransform(C.Matrix4.IDENTITY);
      let px = x, py = y, pz = ground + eyeH + 0.4, hd = this.aim.yaw, pt = this.aim.pitch;
      if (vw === 'driver') {
        const f = dims.length * 0.38;
        px += Math.sin(hull * DEG) * f; py += Math.cos(hull * DEG) * f;
        pz = ground + dims.height * 0.7; hd = yaw; pt = (vis.b?.pi || 0) - 2;
      } else if (vw === 'commander') {
        pz = ground + dims.height + 1.1; hd = yaw; pt = pitch - 4;
      }
      cam.setView({ destination: this.g.frame.cart(px, py, pz), orientation: { heading: hd * DEG, pitch: pt * DEG, roll: 0 } });
      cam.frustum.fov = C.Math.toRadians(vw === 'scope' ? 55 / this.zoom : vw === 'driver' ? 75 : 65);
      return;
    }
    cam.frustum.fov = C.Math.toRadians(60);
    const center = this.g.frame.cart(x, y, ground + eyeH);
    const base = Math.max(14, dims.length * 2.6) * this.rangeMul;
    let range = base, pt = (vw === 'chase' ? pitch - 12 : -14);
    if (this.camMode === 'follow' && vw === 'chase') pt = this.followPitch;
    if (vw === 'chase_far') { range = base * 3.2; pt = -22; }
    if (vw === 'orbit') { range = base * 1.6; pt = -12; }
    if (vw === 'top') { range = Math.max(180, base * 9); pt = -89; }
    cam.lookAt(center, new C.HeadingPitchRange(yaw * DEG, pt * DEG, range));
  }
}

export const CAM_VIEWS = [
  { id: 'chase', label: 'CHASE' },
  { id: 'chase_far', label: 'CHASE FAR' },
  { id: 'hull', label: 'HULL CHASE' },
  { id: 'driver', label: "DRIVER'S VIEW" },
  { id: 'commander', label: "COMMANDER'S HATCH" },
  { id: 'orbit', label: 'CINEMATIC ORBIT' },
  { id: 'top', label: 'TACTICAL TOP-DOWN' },
];

export function hourOf(tod) {
  return { DAWN: 6.2, DAY: 12.5, EVENING: 17.6, NIGHT: 23.5, DYNAMIC: 5.5 }[tod] ?? 12.5;
}
