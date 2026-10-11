// Direct control (DC-1..3): drive a vehicle, traverse the turret, fire; HUD and gunner's scope.
import { angleDiff, bearing, clamp, compass, norm360 } from './geo.js';
import { audio } from './ground/audio.js';

const DEG = Math.PI / 180;
const esc = (t) => String(t ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const ZOOMS = [1, 2, 4, 8, 12];
const SENSORS = ['DAY', 'NV', 'THERMAL'];
const GROUND_LABEL = { ROAD: 'ROAD / CORRIDOR', GRASS: 'FARMLAND', SAND: 'SOFT SAND', MUD: 'MUD', SNOW: 'SNOW', ICE: 'ICE', ROCK: 'ROCK / SCREE', SALT: 'SALT PAN', MARSH: 'MARSH', WATER: 'WATER', AIR: 'AIRBORNE' };

export class DriveController {
  constructor({ g, scene, socket, session, el, onExit }) {
    // scene: any renderer exposing canvas, setInputs, aimPoint, project (GroundView)
    this.scene = scene; this.socket = socket; this.session = session; this.el = el; this.onExit = onExit;
    this.unitId = null;
    this.avatarId = null; // the player's own soldier
    this.ride = false;    // true while the soldier sits in a vehicle (F to get out)
    this.crouch = false;
    this.keys = new Set();
    this.throttle = 0; this.steer = 0; this.climb = 0;
    this.firing = false; this.fireOnce = false;
    this.weapon = 0;
    this.scopeOn = false;
    this.zoomIdx = 0;
    this.sensorIdx = 0;
    this.canvas = el.querySelector('#scope-canvas');
    this.ctx = this.canvas.getContext('2d');
    this.onKeyDown = (e) => this.key(e, true);
    this.onKeyUp = (e) => this.key(e, false);
    this.onMouse = (e) => this.mouse(e);
    this.onDown = (e) => {
      if (!this.unitId) return;
      audio.unlock();
      if (document.pointerLockElement !== this.scene.canvas) { this.scene.canvas.requestPointerLock(); return; }
      if (e.button === 0) { this.firing = true; this.fireOnce = true; }
      if (e.button === 2 && !this.scopeOn) { this.adsHeld = true; this.toggleScope(); } // hold RMB = aim down sights
    };
    this.onUp = (e) => {
      if (e.button === 0) this.firing = false;
      if (e.button === 2 && this.adsHeld) { this.adsHeld = false; if (this.scopeOn) this.toggleScope(); }
    };
    this.onCtx = (e) => { if (this.unitId) e.preventDefault(); };
    this.markers = [];
    this.dmg = [];
    this.pending = new Map();
    this.sens = +(localStorage.getItem('wg_sens') || 1);
    this.onWheel = (e) => {
      if (!this.unitId) return;
      e.preventDefault();
      if (this.scopeOn && this.inf) return;
      if (this.inf && this.sk && this.weapons.length > 1) { this.selectWeapon((this.weapon + (e.deltaY > 0 ? 1 : -1) + this.weapons.length) % this.weapons.length); return; }
      if (this.scopeOn) this.setZoom(this.zoomIdx + (e.deltaY < 0 ? 1 : -1));
      else this.scene.rangeMul = Math.min(6, Math.max(0.35, this.scene.rangeMul * (e.deltaY > 0 ? 1.15 : 0.87)));
    };
    this.raf = () => { if (this.unitId) { this.draw(); requestAnimationFrame(this.raf); } };
  }

  async start(unitId, { possess = true } = {}) {
    if (possess) await this.socket.request('possess', { unit_id: unitId });
    const first = !this.unitId;
    this.ride = false;
    this.bind(unitId);
    if (!first) return;
    this.scene.canvas.addEventListener('contextmenu', this.onCtx);
    this.loadMinimap();
    this.el.classList.add('on');
    document.getElementById('app')?.classList.add('driving');
    this.resize();
    window.addEventListener('keydown', this.onKeyDown);
    window.addEventListener('keyup', this.onKeyUp);
    document.addEventListener('mousemove', this.onMouse);
    this.scene.canvas.addEventListener('mousedown', this.onDown);
    window.addEventListener('mouseup', this.onUp);
    this.scene.canvas.addEventListener('wheel', this.onWheel, { passive: false });
    window.addEventListener('resize', this._rs = () => this.resize());
    this.timer = setInterval(() => this.send(), 50);
    requestAnimationFrame(this.raf);
  }

  /** Point the controls, camera and HUD at a unit (the soldier, or the vehicle the soldier sits in). */
  bind(unitId) {
    const u = this.scene.view(unitId);
    if (!u) return;
    this.unitId = unitId;
    this.spec = this.session.catalogue.platforms[u.p];
    this.sk = !!this.scene.village; // Village Skirmish
    this.wpKey = (u.wp || this.spec.weapons || []).join(',');
    this.weapons = (u.wp || this.spec.weapons || []).map((w) => this.session.catalogue.weapons[w]).filter(Boolean);
    this.weapon = u.wp ? Math.min(u.cw ? u.wp.indexOf(u.cw) : 0, this.weapons.length - 1) : 0;
    if (this.weapon < 0) this.weapon = 0;
    this.air = !!this.spec.air;
    this.inf = this.spec.cls === 'INF';
    if (u.av) this.avatarId = unitId;
    this.scopeOn = false; this.adsHeld = false;
    this.zoomIdx = 0; this.scene.zoom = 1;
    this.scene.aim.yaw = u.ty ?? u.h;
    this.scene.aim.pitch = this.air ? -12 : 0;
    this.scene.drive(unitId);
    this.scene.setInputs(false);
    this.scene.drivenId = unitId;
    this.scene.setLocalMove(this.inf ? { id: unitId, fwd: 0, strafe: 0, sprint: false, crouch: false, pos: null } : null);
    audio.unlock();
    if (this.inf) audio.stopEngine();
    else audio.startEngine(this.spec.hover ? 'hover' : 'tracks');
    this.el.classList.toggle('inf', this.inf);
    const $ = (id) => this.el.querySelector(id);
    $('#h-hp-l').textContent = this.inf ? 'HEALTH' : 'HULL';
    $('#h-fuel-row').style.display = this.inf ? 'none' : '';
    $('#h-help').textContent = this.inf && this.sk
      ? 'WASD move · Shift sprint · C crouch · LMB fire · hold RMB aim · R reload · F pick up / drive · H medkit · 1-3 / wheel weapon · V 1st/3rd person · M map · Enter chat · Esc mouse'
      : this.inf
      ? 'WASD move · Shift sprint · C crouch · mouse aim · LMB fire · hold RMB aim · V 1st/3rd person · F get in vehicle · T call a ride · 1/2 weapon · N NV/thermal · Enter chat · Esc release mouse'
      : `W/S drive · A/D steer · mouse aim · LMB fire · hold RMB aim/scope · hold Alt free-look · 1-4 weapon · C camera · V scope · wheel zoom · N NV/thermal${this.ride ? ` · ${this.air ? 'G' : 'F'} get out` : ''} · Enter chat · Esc exit`;
    this.flashView();
  }
  cmd(type, extra = {}) { return this.socket.request('cmd', { cmd: { type, ...extra } }).catch((e) => this.marker(e.message.toUpperCase(), '#ffb547', 1.6, false)); }
  selectWeapon(i) {
    if (i < 0 || i >= this.weapons.length || i === this.weapon) return;
    const ads = this.scopeOn;
    if (ads) this.toggleScope();
    this.weapon = i;
    if (ads && this.inf) this.toggleScope();
  }
  /** F in the village: pick up the item at your feet, else drive the vehicle next to you. */
  interact(u) {
    const it = this.sk && this.scene.loot ? this.scene.loot.nearest(u.x, u.y, u.z) : null;
    if (it) { this.cmd('pick', { item: it.id }); return; }
    this.cmd('enter');
  }

  async stop(notify = true) {
    if (!this.unitId) return;
    if (this.ride && notify) this.cmd('exit');
    this.ride = false;
    this.scene.setLocalMove(null);
    this.scene.infFP = false;
    clearInterval(this.timer);
    const id = this.unitId;
    this.unitId = null;
    window.removeEventListener('keydown', this.onKeyDown);
    window.removeEventListener('keyup', this.onKeyUp);
    document.removeEventListener('mousemove', this.onMouse);
    this.scene.canvas.removeEventListener('mousedown', this.onDown);
    window.removeEventListener('mouseup', this.onUp);
    this.scene.canvas.removeEventListener('wheel', this.onWheel);
    window.removeEventListener('resize', this._rs);
    if (document.pointerLockElement) document.exitPointerLock();
    this.el.classList.remove('on');
    document.getElementById('app')?.classList.remove('driving');
    this.scopeOn = false;
    this.scene.zoom = 1;
    this.scene.drivenId = null;
    this.scene.lowHp = false;
    this.scene.freeLookActive = false;
    audio.stopEngine();
    this.scene.canvas.removeEventListener('contextmenu', this.onCtx);
    this.scene.freeCam();
    if (notify) this.socket.request('possess', { unit_id: null }).catch(() => {});
    this.onExit && this.onExit(id);
  }

  resize() { this.canvas.width = innerWidth; this.canvas.height = innerHeight; }

  key(e, down) {
    if (!this.unitId || e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    const k = e.key.toLowerCase();
    if (down) this.keys.add(k); else this.keys.delete(k);
    if (!down && k === 'alt') { this.scene.freeLookActive = false; e.preventDefault(); }
    if (!down) return;
    if (k === 'alt') { e.preventDefault(); this.scene.freeLookActive = true; return; }
    if (k === 'escape') this.stop();
    else if (k === 'n') { this.sensorIdx = (this.sensorIdx + 1) % SENSORS.length; this.scene.setSensor(SENSORS[this.sensorIdx]); }
    else if (k >= '1' && k <= '4') this.selectWeapon(+k - 1);
    else if (k === ' ' && this.spectating) this.specIdx = (this.specIdx || 0) + 1;
    else if (k === ' ') { this.fireOnce = true; this.firing = true; setTimeout(() => (this.firing = false), 120); }
    else if (k === 'm' && this.sk) this.bigMap = !this.bigMap;
    else if (this.inf) {
      const me = this.scene.view(this.unitId);
      if (k === 'c') this.crouch = !this.crouch;
      else if (k === 'v') { this.scene.infFP = !this.scene.infFP; this.flashView(); }
      else if (k === 'f' || k === 'e') { if (me) this.interact(me); }
      else if (k === 'r' && this.sk) this.cmd('reload');
      else if (k === 'h' && this.sk) this.cmd('heal');
      else if (k === 't' && !this.sk) { this.cmd('summon'); this.marker('RIDE REQUESTED', '#9dff6a', 1.6, false); }
    } else {
      if (k === 'v') this.toggleScope();
      else if (k === 'c') { if (this.scopeOn) this.toggleScope(); this.scene.cycleView(e.shiftKey ? -1 : 1); this.flashView(); }
      else if (k === 'z') this.setZoom(this.zoomIdx + 1 >= ZOOMS.length ? 1 : this.zoomIdx + 1);
      else if (this.ride && (k === 'g' || (k === 'f' && !this.air))) this.cmd('exit');
    }
    if (['w', 'a', 's', 'd', ' ', 'r', 'f', 'c', 'h', 'm', 'shift'].includes(k)) e.preventDefault();
  }
  flashView() {
    const el = this.el.querySelector('#h-view');
    if (!el) return;
    el.textContent = this.inf ? `VIEW · ${this.scopeOn ? (this.zoomIdx >= 2 ? `SCOPE ×${this.scene.zoom}` : 'AIMING') : this.scene.infFP ? 'FIRST PERSON' : 'THIRD PERSON'}`
      : `VIEW · ${this.scopeOn ? "GUNNER'S SCOPE" : this.scene.viewLabel}`;
    el.classList.remove('flash'); void el.offsetWidth; el.classList.add('flash');
  }

  toggleScope() {
    if (this.inf) {
      this.scopeOn = !this.scopeOn;
      const w = this.weapons[this.weapon];
      const zoom = w?.zoom || (w?.type === 'MISSILE' ? 4 : 1.6);
      if (this.scopeOn) { this.scene.scope(this.unitId); this.zoomIdx = zoom >= 7 ? 3 : zoom >= 3.5 ? 2 : 0; this.scene.zoom = zoom; }
      else { this.scene.drive(this.unitId); this.zoomIdx = 0; this.scene.zoom = 1; }
      this.flashView();
      return;
    }
    this.scopeOn = !this.scopeOn;
    if (this.scopeOn) { this.scene.scope(this.unitId); if (this.zoomIdx === 0) this.setZoom(2); }
    else { this.scene.drive(this.unitId); this.setZoom(0); }
    this.flashView();
  }
  setZoom(i) {
    this.zoomIdx = clamp(i, this.scopeOn ? 1 : 0, ZOOMS.length - 1);
    this.scene.zoom = ZOOMS[this.zoomIdx];
  }
  mouse(e) {
    if (!this.unitId || document.pointerLockElement !== this.scene.canvas) return;
    const sens = (0.12 * this.sens) / Math.max(1, this.scene.zoom || 1);
    if (this.scene.freeLookActive && !this.scopeOn) { // Alt held: look around without moving the turret
      const fl = this.scene.freeLook;
      fl.yaw = clamp(fl.yaw + e.movementX * sens, -150, 150);
      fl.pitch = clamp(fl.pitch - e.movementY * sens, -40, 35);
      return;
    }
    this.scene.aim.yaw = norm360(this.scene.aim.yaw + e.movementX * sens);
    const lo = this.air ? -70 : this.inf ? -70 : -10, hi = this.air ? 10 : this.inf ? 70 : 25;
    this.scene.aim.pitch = clamp(this.scene.aim.pitch - e.movementY * sens, lo, hi);
  }

  /** World aim point under the crosshair. */
  aimPoint() { return this.scene.aimPoint(this.scopeOn, this.unitId); }

  /** Soldier movement keys → intent (also feeds client-side prediction every frame). */
  infInput() {
    const k = this.keys;
    const fwd = k.has('w') ? 1 : k.has('s') ? -1 : 0, strafe = k.has('d') ? 1 : k.has('a') ? -1 : 0;
    const sprint = k.has('shift') && fwd > 0 && !this.scopeOn;
    if (sprint) this.crouch = false;
    const m = { id: this.unitId, fwd, strafe, sprint, crouch: this.crouch };
    this.scene.setLocalMove(m);
    return m;
  }

  send() {
    const k = this.keys;
    if (this.inf) {
      const m = this.infInput();
      this.throttle = m.fwd;
      this.aim = this.aimPoint();
      const w = this.weapons[this.weapon];
      const fire = this.fireOnce || (w && (w.type === 'AUTOCANNON' || w.auto) && this.firing);
      this.fireOnce = false;
      this.socket.send('drive', {
        unit_id: this.unitId, throttle: m.fwd, strafe: m.strafe, sprint: m.sprint, crouch: m.crouch, ads: this.scopeOn,
        turret_yaw: +this.scene.aim.yaw.toFixed(1), gun_pitch: +this.scene.aim.pitch.toFixed(1), fire, weapon: this.weapon,
        aim: this.aim ? this.aim.map((v) => +v.toFixed(1)) : null,
      });
      return;
    }
    const tgtThr = k.has('w') ? 1 : k.has('s') ? -0.6 : 0;
    this.throttle += clamp(tgtThr - this.throttle, -0.12, 0.12);
    if (Math.abs(this.throttle) < 0.03 && !tgtThr) this.throttle = 0;
    this.steer = k.has('a') ? -1 : k.has('d') ? 1 : 0;
    this.climb = k.has('r') ? 1 : k.has('f') ? -1 : 0;
    this.aim = this.aimPoint();
    const w = this.weapons[this.weapon];
    const auto = w && w.type === 'AUTOCANNON';
    const fire = this.fireOnce || (auto && this.firing);
    this.fireOnce = false;
    this.socket.send('drive', {
      unit_id: this.unitId, throttle: +this.throttle.toFixed(2), steer: this.steer, climb: this.climb,
      turret_yaw: +this.scene.aim.yaw.toFixed(1), gun_pitch: +this.scene.aim.pitch.toFixed(1), fire, weapon: this.weapon,
      aim: this.aim ? this.aim.map((v) => +v.toFixed(1)) : null,
    });
  }

  // ── HUD ────────────────────────────────────────────────────────────────
  draw() {
    // follow the player's own soldier into and out of vehicles
    const me = this.avatarId ? this.scene.view(this.avatarId) : null;
    if (me) {
      if (this.unitId === this.avatarId && me.mt && this.scene.view(me.mt)) { this.ride = true; this.bind(me.mt); return; }
      if (this.ride && !me.mt) { this.ride = false; this.bind(this.avatarId); return; }
    }
    const u = this.scene.view(this.unitId);
    if (!u) return;
    if (this.inf) this.infInput();
    if (this.inf && u.wp && u.wp.join(',') !== this.wpKey) { // picked something up
      const before = this.wpKey ? this.wpKey.split(',') : [];
      this.wpKey = u.wp.join(',');
      this.weapons = u.wp.map((w) => this.session.catalogue.weapons[w]).filter(Boolean);
      const fresh = u.wp.findIndex((w) => !before.includes(w));
      if (fresh >= 0) this.weapon = fresh;
      if (this.weapon >= this.weapons.length) this.weapon = 0;
    }
    const now = performance.now() / 1000;
    const dt = Math.min(0.1, now - (this._t || now)); this._t = now;
    const servo = this._ty == null ? 0 : Math.abs(angleDiff(this._ty, this.scene.aim.yaw)) / Math.max(0.016, dt);
    this._ty = this.scene.aim.yaw;
    audio.updateEngine(u.v, this.throttle, servo);
    const $ = (id) => this.el.querySelector(id);
    const dead = u.st === 'DESTROYED';
    this.scene.lowHp = !dead && u.hp / u.mhp < 0.35;
    if (dead && !u.av) {
      if (this.avatarId && this.scene.view(this.avatarId)) { this.ride = false; this.bind(this.avatarId); } else this.stop();
      return;
    }
    if (dead && this.sk) this.spectate(u);
    else {
      if (this.spectating) { this.spectating = null; this.scene.drive(this.unitId); }
      $('#h-respawn').innerHTML = dead ? `KILLED IN ACTION<small>Respawning at your HQ in ${Math.ceil(u.rs || 0)} s</small>` : '';
    }
    $('#h-prompt').innerHTML = dead ? '' : this.prompt(u);
    this.drawSquad(u);
    const kph = Math.abs(u.v * 3.6);
    $('#h-speed').textContent = kph.toFixed(0);
    if (this.inf) {
      const L = this.scene.local || {};
      if ((L.fwd || L.strafe) && !dead && now - (this._stepT || 0) > (u.sw ? 0.9 : L.sprint ? 0.31 : L.crouch ? 0.7 : 0.5)) { this._stepT = now; audio.step(u.sw ? 'WATER' : u.gr, L.sprint); }
      $('#h-cap').textContent = `STANCE · ${u.sw ? 'SWIMMING' : L.crouch ? 'CROUCH' : 'STANDING'}`;
      $('#h-thr').style.width = `${Math.min(100, kph / 21 * 100)}%`;
      $('#h-dir').textContent = u.sw ? 'SWIM' : L.sprint ? 'SPRINT' : L.fwd || L.strafe ? (L.crouch ? 'SNEAK' : 'WALK') : 'STILL';
    } else {
      $('#h-cap').textContent = `MAX ${Math.round(u.cap || 0)}`;
      $('#h-thr').style.width = `${Math.abs(this.throttle) * 100}%`;
      $('#h-dir').textContent = this.throttle < -0.02 ? 'REVERSE' : this.throttle > 0.02 ? 'FORWARD' : 'NEUTRAL';
    }
    $('#h-heading').textContent = `${Math.round(u.h).toString().padStart(3, '0')}° ${compass(u.h)}`;
    $('#h-slope').textContent = this.inf ? `SLOPE ${u.sl.toFixed(0)}°` : this.air ? `ALT ${Math.round(u.alt)} m AGL` : `SLOPE ${u.sl.toFixed(0)}°  GRADE ${u.gd > 0 ? '+' : ''}${u.gd.toFixed(0)}%`;
    $('#h-ground').textContent = GROUND_LABEL[u.gr] || u.gr;
    const hp = (u.hp / u.mhp) * 100, fuel = u.fm ? (u.fu / u.fm) * 100 : 100;
    this.gauge('#h-hp', hp); this.gauge('#h-fuel', fuel); this.gauge('#h-morale', u.m);
    $('#h-hp-v').textContent = `${hp.toFixed(0)}%`; $('#h-fuel-v').textContent = `${Math.round(u.fu)} L`; $('#h-morale-v').textContent = Math.round(u.m);
    const flags = [];
    if (u.mk) flags.push(['IMMOBILISED', '#ff4b3e']);
    if (u.fk) flags.push(['WEAPONS DAMAGED', '#ff4b3e']);
    if (u.sw) flags.push(['SWIMMING', '#2ef2ff']);
    if (u.fu <= 0 && !this.inf) flags.push(['NO FUEL', '#ff4b3e']);
    if (this.ride) flags.push([`CREWED BY ${(me?.n || 'YOU').toUpperCase()}`, '#9dff6a']);
    if (u.aps) flags.push([`APS ×${u.aps}`, '#6cff9e']);
    if (u.m < 30 && !this.sk) flags.push(['SHAKEN', '#ffb547']);
    if (this.sk && this.inf) {
      if (u.ve) flags.push(['VEST', '#9dd3ff']);
      if (u.hl) flags.push(['HELMET', '#9dd3ff']);
      flags.push([`MEDKIT ×${u.md || 0}${u.md ? ' · H' : ''}`, u.md ? '#ff7a7a' : '#777']);
      if (u.ht > 0) flags.push([`HEALING ${u.ht.toFixed(1)}s`, '#7dffb0']);
    }
    $('#h-flags').innerHTML = flags.map(([t, c]) => `<span style="color:${c};border-color:${c}">${t}</span>`).join('');
    $('#h-weapons').innerHTML = this.weapons.map((w, i) => {
      if (w.mag) {
        const mg = u.mg?.[w.id] ?? 0, res = u.am?.[w.id] ?? 0;
        const st = u.rlt > 0 && i === this.weapon ? `<span style="color:#ffb547">RELOADING ${u.rlt.toFixed(1)}s</span>` : mg <= 0 && res <= 0 ? 'EMPTY' : '';
        return `<div class="${i === this.weapon ? 'act' : ''}">${i + 1} ${w.name.toUpperCase()} · <b style="font-size:1.25em">${mg}</b> / ${res} ${st}</div>`;
      }
      const ammo = u.am?.[w.id] ?? 0, rl = u.rl?.[w.id] ?? 0;
      const st = ammo <= 0 ? 'EMPTY' : rl > 0 ? `RELOAD ${rl.toFixed(1)}s` : 'READY';
      return `<div class="${i === this.weapon ? 'act' : ''}">${i + 1} ${w.name.toUpperCase()} · ${w.round} · <b>${ammo}</b> · ${st}</div>`;
    }).join('');
    $('#h-kills').textContent = `KILLS ${u.k}`;
    this.drawScope(u);
    this.drawMarkers(u, now);
    this.drawMinimap(u);
    if (this.sk) this.drawSkirmish(u);
  }

  /** Context prompt: get into a nearby friendly vehicle, or get out of this one. */
  prompt(u) {
    if (this.ride) return `<b>${this.air ? 'G' : 'F'}</b> GET OUT`;
    if (!this.inf || u.sw) return '';
    if (this.sk && this.scene.loot) {
      const it = this.scene.loot.nearest(u.x, u.y, u.z);
      if (it) return `<b>F</b> PICK UP ${esc(this.scene.loot.label(it.k)).toUpperCase()}`;
    }
    let best = null, bd = 1e9;
    for (const v of this.scene.cur?.units || []) {
      if ((v.s !== u.s && !(this.sk && v.s === 'NEUT')) || v.st === 'DESTROYED' || v.hvt || v.c === 'INF') continue;
      if (v.ctl && v.ctl !== 'AI' && v.ctl !== this.playerId) continue;
      const sp = this.session.catalogue.platforms[v.p];
      if (sp?.air && v.alt > 30) continue;
      const dims = sp?.dims || { length: 8, width: 3.5 };
      const d = Math.hypot(v.x - u.x, v.y - u.y);
      const reach = Math.max(dims.length, dims.width) / 2 + (this.sk ? 3 : sp?.air ? 6 : 5); // same reach as the server
      if (d < reach && d < bd) { best = v; bd = d; }
    }
    if (best) return `<b>F</b> ${this.sk ? 'DRIVE' : 'TAKE THE CONTROLS'} · ${best.n.toUpperCase()}`;
    const ride = (this.scene.cur?.units || []).find((v) => v.pk && v.pk === this.playerId && v.st !== 'DESTROYED');
    return ride ? `<span style="color:#9dff6a">RIDE INBOUND · ${esc(ride.n)} · ${Math.round(Math.hypot(ride.x - u.x, ride.y - u.y))} m</span>` : '';
  }
  /** Squad: every human player's soldier on this side (health, vehicle, range). */
  drawSquad(u) {
    const el = this.el.querySelector('#h-squad');
    const fr = this.scene.cur;
    if (!fr || (this._sqT && performance.now() - this._sqT < 300)) return;
    this._sqT = performance.now();
    const mine = this.avatarId ? fr.units.find((x) => x.id === this.avatarId) : null;
    const ref = mine || u;
    const name = (id) => fr.units.find((x) => x.id === id)?.n || 'vehicle';
    el.innerHTML = fr.units.filter((x) => (x.av || (this.sk && x.bot)) && x.s === ref.s).map((x) => {
      const you = x.id === this.avatarId;
      const dead = x.st === 'DESTROYED';
      const where = dead ? (this.sk ? 'DOWN' : `KIA · ${Math.ceil(x.rs || 0)}s`) : x.mt ? `IN ${name(x.mt).toUpperCase()}` : you ? 'YOU' : `${Math.round(Math.hypot(x.x - ref.x, x.y - ref.y))} m`;
      const hp = dead ? 0 : (x.hp / x.mhp) * 100;
      return `<div class="${dead ? 'down' : ''}">${you ? '▶ ' : ''}${esc(x.n)}${x.bot ? ' <span class="text-muted2">(bot)</span>' : ''} · <span class="text-muted2">${where}</span><span class="hp"><i style="width:${hp}%;background:${hp < 35 ? '#ff4b3e' : '#6cff9e'}"></i></span></div>`;
    }).join('') || '<div class="text-muted2">—</div>';
  }

  // ── combat feedback (called by the battle page for every frame) ────────────
  onEvents(events) {
    if (!this.unitId) return;
    const now = performance.now() / 1000;
    const me = this.unitId;
    for (const ev of events) {
      if (ev.t === 'FIRE' && ev.shooter === me && ev.target) this.pending.set(ev.target, { t: now + (ev.flight || 0), hit: ev.hit });
      if (ev.t === 'FIRE' && ev.target === me && this.sk) { const pos = this.scene.rendered(me); if (pos && ev.from) this.dmg.push({ brg: bearing(pos[0], pos[1], ev.from[0], ev.from[1]), t0: now, hit: ev.hit }); }
      if (ev.t === 'PICK' && ev.unit === this.avatarId) audio.ui('pick');
      if (ev.t === 'IMPACT' && ev.target && ev.effect === 'DESTROYED') (this.headKill ||= {})[ev.target] = ev.part === 'head';
      if (ev.t === 'KILL' && this.sk) this.feedKill(ev);
      if (this.sk && ev.t === 'FIRE' && ev.shooter === me) continue; // no HIT/MISS text spam with small arms
      if (this.sk && ev.t === 'IMPACT' && ev.hit && this.pending.has(ev.target)) {
        const head = ev.effect === 'HEADSHOT', kill = ev.effect === 'DESTROYED';
        this.marker(kill ? 'ELIMINATED' : head ? 'HEADSHOT' : '', kill ? '#ff5a4a' : head ? '#ffd34d' : '#ffffff', kill ? 1.6 : 0.6, true, kill);
        this.pending.delete(ev.target);
        continue;
      }
      if (ev.t === 'FIRE' && ev.shooter === me && ev.fx !== 'MISSILE' && ev.fx !== 'SHELL' && ev.fx !== 'ROCKET') {
        setTimeout(() => this.marker(ev.hit ? 'HIT' : 'MISS', ev.hit ? '#ffffff' : '#9aa', 0.9, ev.hit), (ev.flight || 0) * 1000);
      }
      if (ev.t === 'IMPACT' && ev.target && this.pending.has(ev.target) && ev.hit) {
        const map = { NO_PEN: ['NO PENETRATION', '#ffb547'], DAMAGED: ['PENETRATION', '#7dffb0'], MOBILITY_KILL: ['PENETRATION · MOBILITY KILL', '#7dffb0'],
          FIREPOWER_KILL: ['PENETRATION · FIREPOWER KILL', '#7dffb0'], DESTROYED: ['TARGET DESTROYED', '#ff6a5a'] };
        const [txt, col] = map[ev.effect] || ['HIT', '#fff'];
        setTimeout(() => this.marker(txt, col, 1.4, true), Math.max(0, (this.pending.get(ev.target).t - now) * 1000));
        this.pending.delete(ev.target);
      }
      if (ev.t === 'KILL' && ev.attacker === me) setTimeout(() => { if (!this.sk) this.marker(`DESTROYED ${ev.victim}`, '#ff6a5a', 2.6, true, true); audio.ui('kill'); }, 300);
      if ((ev.t === 'FIRE' && ev.target === me) || (ev.t === 'IMPACT' && ev.target === me && ev.hit)) {
        const pos = this.scene.rendered(me);
        if (pos && ev.from) this.dmg.push({ brg: bearing(pos[0], pos[1], ev.from[0], ev.from[1]), t0: now, hit: ev.t === 'IMPACT' });
        if (ev.t === 'IMPACT' && !this.sk) this.marker(`HIT TAKEN · ${String(ev.effect || '').replace('_', ' ')}`, '#ff4b3e', 1.6, false);
      }
    }
    this.dmg = this.dmg.filter((d) => now - d.t0 < 3);
  }
  marker(text, color, life, hitX, big = false) {
    this.markers.push({ text, color, t0: performance.now() / 1000, life, hitX, big });
    if (hitX) audio.ui('hit');
  }
  drawMarkers(u, now) {
    const g = this.ctx, W = this.canvas.width, H = this.canvas.height;
    let cx = W / 2, cy = H / 2;
    if (!this.scopeOn && this.aim) { const p = this.scene.project(this.aim); if (p) { cx = p.x; cy = p.y; } }
    this.markers = this.markers.filter((m) => now - m.t0 < m.life);
    this.markers.forEach((m, i) => {
      const k = 1 - (now - m.t0) / m.life;
      g.save(); g.globalAlpha = Math.min(1, k * 2);
      if (m.hitX && i === this.markers.length - 1 && now - m.t0 < 0.5) { // hit marker X
        g.strokeStyle = m.color; g.lineWidth = 3; const r0 = 9, r1 = 20;
        for (const [sx, sy] of [[1, 1], [1, -1], [-1, 1], [-1, -1]]) { g.beginPath(); g.moveTo(cx + sx * r0, cy + sy * r0); g.lineTo(cx + sx * r1, cy + sy * r1); g.stroke(); }
      }
      if (!m.text) { g.restore(); return; }
      g.font = `700 ${m.big ? 30 : 20}px Rajdhani, sans-serif`; g.textAlign = 'center';
      g.lineWidth = 4; g.strokeStyle = 'rgba(0,0,0,.7)';
      const y = cy + 70 + (this.markers.length - 1 - i) * 26;
      g.strokeText(m.text, cx, y); g.fillStyle = m.color; g.fillText(m.text, cx, y);
      g.restore();
    });
    // damage direction indicators (point toward the shooter)
    const yaw = this.scene.aim.yaw + (this.scene.freeLook?.yaw || 0);
    for (const d of this.dmg) {
      const k = 1 - (now - d.t0) / 3;
      const a = angleDiff(yaw, d.brg) * DEG;
      g.save(); g.translate(W / 2, H / 2); g.rotate(a);
      g.strokeStyle = `rgba(255,60,40,${0.85 * k})`; g.lineWidth = d.hit ? 14 : 7;
      g.beginPath(); g.arc(0, 0, Math.min(W, H) * 0.3, -Math.PI / 2 - 0.22, -Math.PI / 2 + 0.22); g.stroke();
      g.fillStyle = `rgba(255,60,40,${0.9 * k})`; const R = Math.min(W, H) * 0.3 + 14;
      g.beginPath(); g.moveTo(0, -R - 14); g.lineTo(-10, -R); g.lineTo(10, -R); g.fill();
      g.restore();
    }
  }

  // ── Village Skirmish: spectate, alive counter, kill feed, full map ──────────
  spectate(u) {
    const el = this.el.querySelector('#h-respawn');
    const mates = (this.scene.cur?.units || []).filter((x) => x.s === u.s && (x.av || x.bot) && x.st !== 'DESTROYED');
    if (!mates.length) { el.innerHTML = 'ELIMINATED<small>Your whole squad is down</small>'; return; }
    const t = mates[(this.specIdx || 0) % mates.length];
    if (this.spectating !== t.id) { this.spectating = t.id; this.scene.follow(t.id); }
    el.innerHTML = `ELIMINATED<small>Spectating ${esc(t.n)} · Space: next squad mate · Esc: leave</small>`;
  }
  unitLabel(id) {
    const u = this.scene.cur?.units.find((x) => x.id === id);
    if (u) return u.n;
    return String(id || '?').replace(/^[BR]-(BOT|SOL)-/, '').replace(/^V-/, '').toLowerCase().replace(/^\w/, (c) => c.toUpperCase());
  }
  feedKill(ev) {
    const side = (id) => (String(id).startsWith('B-') ? '#6cc4ff' : String(id).startsWith('R-') ? '#ff7a6a' : '#ccc');
    if (String(ev.victim).startsWith('V-')) return;
    const w = ev.weapon ? this.session.catalogue.weapons[ev.weapon]?.name : 'roadkill';
    (this.feed ||= []).push({ t: performance.now() / 1000, a: ev.attacker ? this.unitLabel(ev.attacker) : '—', ac: side(ev.attacker),
      v: this.unitLabel(ev.victim), vc: side(ev.victim), w: w || '', head: !!this.headKill?.[ev.victim], mine: ev.attacker === this.avatarId });
    this.feed = this.feed.slice(-6);
  }
  drawSkirmish(u) {
    const g = this.ctx, W = this.canvas.width, H = this.canvas.height;
    const fr = this.scene.cur;
    const now = performance.now() / 1000;
    // alive counter
    const alive = fr?.alive || {};
    g.save();
    g.font = '700 18px Rajdhani, sans-serif'; g.textAlign = 'center';
    const txt = [`BLUE ${alive.BLUE ?? '-'}`, `RED ${alive.RED ?? '-'}`];
    g.fillStyle = 'rgba(0,0,0,.45)'; g.fillRect(W / 2 - 110, 98, 220, 28);
    g.fillStyle = '#6cc4ff'; g.fillText(txt[0], W / 2 - 52, 118);
    g.fillStyle = '#fff'; g.fillText('ALIVE', W / 2, 118);
    g.fillStyle = '#ff7a6a'; g.fillText(txt[1], W / 2 + 52, 118);
    // kill feed under the minimap
    this.feed = (this.feed || []).filter((f) => now - f.t < 7);
    g.textAlign = 'right'; g.font = '600 14px Rajdhani, sans-serif';
    this.feed.forEach((f, i) => {
      const y = 262 + i * 20, x = W - 24;
      g.fillStyle = f.mine ? 'rgba(255,211,77,.25)' : 'rgba(0,0,0,.4)';
      g.fillRect(x - 300, y - 14, 300, 18);
      let cx = x - 6;
      const put = (t, c) => { g.fillStyle = c; g.fillText(t, cx, y); cx -= g.measureText(t).width + 6; };
      put(f.v, f.vc); put(`[${f.w}${f.head ? ' ✦HEADSHOT' : ''}]`, f.head ? '#ffd34d' : '#ddd'); put(f.a, f.ac);
    });
    g.restore();
    this.el.classList.toggle('bigmap', !!this.bigMap);
    if (this.bigMap) this.drawBigMap(u);
  }
  villageMap() {
    const L = this.scene.village.L, half = L.play_half + 40, px = 2;
    const c = document.createElement('canvas');
    c.width = c.height = Math.round(2 * half * px);
    const g = c.getContext('2d');
    const P = (x, y) => [(x + half) * px, (half - y) * px];
    g.fillStyle = '#56603f'; g.fillRect(0, 0, c.width, c.height);
    // lake
    const lk = L.lake;
    g.fillStyle = '#3f6f8e'; g.beginPath();
    for (let a = 0; a <= 64; a++) {
      const th = a / 64 * Math.PI * 2, r = lk.r * (1 + lk.a3 * Math.sin(3 * th + lk.p3) + lk.a5 * Math.sin(5 * th + lk.p5));
      const [x, y] = P(lk.cx + Math.cos(th) * r, lk.cy + Math.sin(th) * r); a ? g.lineTo(x, y) : g.moveTo(x, y);
    }
    g.fill();
    // roads
    g.strokeStyle = '#b9a27d'; g.lineCap = 'round'; g.lineJoin = 'round';
    for (const r of L.roads) { g.lineWidth = r.width * px; g.beginPath(); r.path.forEach(([x, y], i) => { const [a, b] = P(x, y); i ? g.lineTo(a, b) : g.moveTo(a, b); }); g.stroke(); }
    // buildings
    for (const b of L.buildings) {
      const rect = (cx, cy, w, d, rot, fill, stroke) => {
        g.save(); const [a, bb] = P(cx, cy); g.translate(a, bb); g.rotate(-rot);
        if (fill) { g.fillStyle = fill; g.fillRect(-w / 2 * px, -d / 2 * px, w * px, d * px); }
        if (stroke) { g.strokeStyle = stroke; g.lineWidth = 1.5; g.strokeRect(-w / 2 * px, -d / 2 * px, w * px, d * px); }
        g.restore();
      };
      if (b.compound) rect(b.compound.cx, b.compound.cy, b.compound.w, b.compound.d, b.compound.rot, '#8f8a6a', '#a0522d');
      rect(b.cx, b.cy, b.w, b.d, b.rot, b.k === 'school' ? '#e2d29a' : '#cdbf9f', '#5a4a35');
    }
    for (const p of L.props) { const [x, y] = P(p.x, p.y); g.fillStyle = p.k === 'hay' ? '#c9a443' : '#7a5a3a'; g.beginPath(); g.arc(x, y, p.k === 'hay' ? 3.5 : 2, 0, 7); g.fill(); }
    g.strokeStyle = '#ff4b3e'; g.lineWidth = 3; const [bx, by] = P(-L.play_half, L.play_half); g.strokeRect(bx, by, L.play_half * 2 * px, L.play_half * 2 * px);
    this.mapImg = c;
    this.mapImg.complete = true;
    this.mapExt = { x0: -half, y0: -half, w: 2 * half, h: 2 * half };
  }
  drawBigMap(u) {
    const g = this.ctx, W = this.canvas.width, H = this.canvas.height;
    if (!this.mapImg) return;
    const S = Math.min(W, H) * 0.86, x0 = (W - S) / 2, y0 = (H - S) / 2, e = this.mapExt, k = S / e.w;
    const P = (x, y) => [x0 + (x - e.x0) * k, y0 + (e.y0 + e.h - y) * k];
    g.save();
    g.fillStyle = 'rgba(0,0,0,.6)'; g.fillRect(0, 0, W, H);
    g.drawImage(this.mapImg, x0, y0, S, S);
    // 100 m grid with letters / numbers
    g.strokeStyle = 'rgba(255,255,255,.18)'; g.lineWidth = 1; g.font = '600 13px Rajdhani'; g.fillStyle = 'rgba(255,255,255,.7)';
    for (let v = Math.ceil(e.x0 / 100) * 100, n = 0; v <= e.x0 + e.w; v += 100, n++) {
      const [a] = P(v, 0); g.beginPath(); g.moveTo(a, y0); g.lineTo(a, y0 + S); g.stroke(); g.fillText('ABCDEFGH'[n] || '', a + 4, y0 + 14);
      const [, b] = P(0, v); g.beginPath(); g.moveTo(x0, b); g.lineTo(x0 + S, b); g.stroke(); g.fillText(String(n + 1), x0 + 4, b - 4);
    }
    for (const v of this.scene.cur?.units || []) {
      if (v.st === 'DESTROYED') continue;
      const [a, b] = P(v.x, v.y);
      if (v.s === 'NEUT') { g.fillStyle = '#d9d9d9'; g.fillRect(a - 3, b - 3, 6, 6); continue; }
      const mine = v.s === u.s;
      if (v.id === this.avatarId) continue;
      if (v.mt) continue;
      g.fillStyle = mine ? '#9dff6a' : '#ff4b3e';
      g.beginPath(); g.arc(a, b, mine ? 5 : 6, 0, 7); g.fill();
      if (mine) { g.fillStyle = '#fff'; g.fillText(v.n, a + 8, b + 4); }
    }
    const me = this.scene.rendered(this.avatarId || this.unitId) || [u.x, u.y];
    const [a, b] = P(me[0], me[1]);
    g.translate(a, b); g.rotate(this.scene.aim.yaw * DEG);
    g.fillStyle = '#ffd34d'; g.beginPath(); g.moveTo(0, -11); g.lineTo(-7, 8); g.lineTo(0, 4); g.lineTo(7, 8); g.fill();
    g.restore();
    g.save(); g.font = '700 16px Rajdhani'; g.fillStyle = '#fff'; g.textAlign = 'center'; g.fillText('MAP · M to close', W / 2, y0 - 8); g.restore();
  }

  // ── minimap (rotating radar, own heading up) ─────────────────────────────
  loadMinimap() {
    if (this.mapImg) return;
    if (this.scene.village) { this.villageMap(); return; }
    const sc = this.session.scenario;
    this.mapImg = new Image();
    this.mapImg.src = `/api/scenarios/${sc.id}/terrain/layer.png?layer=relief`;
    const m = this.session.terrain?.meta;
    this.mapExt = m ? { x0: m.x0, y0: m.y0, w: (m.cols - 1) * m.cell_m, h: (m.rows - 1) * m.cell_m } : null;
  }
  drawMinimap(u) {
    const g = this.ctx, W = this.canvas.width;
    const R = 105, cx = W - R - 22, cy = R + 22, rangeM = this.sk ? 220 : 3500, k = R / rangeM;
    const pos = this.scene.rendered(this.unitId) || [u.x, u.y];
    const rot = -(this.scene.aim.yaw + (this.scene.freeLook?.yaw || 0)) * DEG; // view direction up
    g.save();
    g.beginPath(); g.arc(cx, cy, R, 0, Math.PI * 2); g.closePath();
    g.fillStyle = 'rgba(5,10,8,.75)'; g.fill();
    g.save(); g.clip();
    g.translate(cx, cy); g.rotate(rot);
    if (this.mapImg?.complete && this.mapExt) {
      const e = this.mapExt;
      g.globalAlpha = 0.75;
      g.drawImage(this.mapImg, (e.x0 - pos[0]) * k, -(e.y0 + e.h - pos[1]) * k, e.w * k, e.h * k);
      g.globalAlpha = 1;
    }
    for (let r = this.sk ? 50 : 1000; r <= rangeM; r += this.sk ? 50 : 1000) { g.strokeStyle = 'rgba(120,255,170,.18)'; g.beginPath(); g.arc(0, 0, r * k, 0, Math.PI * 2); g.stroke(); }
    const fr = this.scene.cur;
    for (const v of fr?.units || []) {
      if (v.st === 'DESTROYED' || v.id === this.unitId) continue;
      const dx = (v.x - pos[0]) * k, dy = -(v.y - pos[1]) * k;
      if (dx * dx + dy * dy > R * R) continue;
      const mine = v.s === u.s;
      const soldier = v.av || v.bot;
      g.fillStyle = v.s === 'NEUT' ? '#9aa39a' : v.hvt ? '#ffd34d' : mine && soldier ? '#9dff6a' : mine ? '#36b8ff' : '#ff4b3e';
      if (v.s === 'NEUT') { if (this.sk) g.fillRect(dx - 2, dy - 2, 4, 4); continue; }
      if (soldier && !v.mt) { g.fillRect(dx - 3, dy - 3, 6, 6); if (mine) { g.font = '600 10px Rajdhani'; g.fillText(v.n, dx + 5, dy - 4); } continue; }
      if (soldier) continue;
      if (v.hvt) { g.save(); g.translate(dx, dy); g.rotate(Math.PI / 4); g.fillRect(-5, -5, 10, 10); g.restore(); }
      else if (['GUNSHIP', 'JET', 'UAV'].includes(v.c)) { g.beginPath(); g.moveTo(dx, dy - 5); g.lineTo(dx - 4, dy + 4); g.lineTo(dx + 4, dy + 4); g.fill(); }
      else { g.beginPath(); g.arc(dx, dy, mine ? 3 : 4, 0, Math.PI * 2); g.fill(); }
    }
    g.restore();
    // view cone + own arrow (always pointing up = where you look)
    g.fillStyle = 'rgba(160,255,200,.12)';
    g.beginPath(); g.moveTo(cx, cy); g.arc(cx, cy, R, -Math.PI / 2 - 0.5, -Math.PI / 2 + 0.5); g.fill();
    g.save(); g.translate(cx, cy); g.rotate(rot + u.h * DEG);
    g.fillStyle = '#b9ffcf'; g.beginPath(); g.moveTo(0, -9); g.lineTo(-6, 7); g.lineTo(0, 3); g.lineTo(6, 7); g.fill();
    g.restore();
    g.strokeStyle = 'rgba(120,255,170,.6)'; g.lineWidth = 1.5; g.beginPath(); g.arc(cx, cy, R, 0, Math.PI * 2); g.stroke();
    // north marker
    g.save(); g.translate(cx, cy); g.rotate(rot); g.fillStyle = '#ffb547'; g.font = '700 13px Rajdhani'; g.textAlign = 'center'; g.fillText('N', 0, -R + 14); g.restore();
    g.restore();
  }
  gauge(sel, pct) {
    const el = this.el.querySelector(sel);
    el.classList.toggle('warn', pct < 50 && pct >= 25);
    el.classList.toggle('crit', pct < 25);
    el.firstElementChild.style.width = `${clamp(pct, 0, 100)}%`;
  }

  drawCrosshair(u, w, cx, cy) {
    const g = this.ctx, W = this.canvas.width;
    const pos = this.scene.rendered(this.unitId);
    const range = this.aim && pos ? Math.hypot(this.aim[0] - pos[0], this.aim[1] - pos[1], this.aim[2] - pos[2]) : null;
    const L = this.scene.local || {};
    if (u.st === 'DESTROYED') return;
    g.save();
    if (this.scopeOn) { // holographic red-dot sight
      g.strokeStyle = 'rgba(255,80,60,.55)'; g.lineWidth = 1.5;
      g.beginPath(); g.arc(cx, cy, 22, 0, Math.PI * 2); g.stroke();
      g.fillStyle = '#ff3b2f'; g.shadowColor = '#ff3b2f'; g.shadowBlur = 8;
      g.beginPath(); g.arc(cx, cy, 2.6, 0, Math.PI * 2); g.fill();
    } else { // dynamic hip-fire crosshair: spreads when moving
      const spread = 7 + (L.sprint ? 14 : L.fwd || L.strafe ? 7 : 0) + (this.firing ? 5 : 0) - (L.crouch ? 3 : 0);
      g.strokeStyle = 'rgba(255,255,255,.92)'; g.lineWidth = 2; g.shadowColor = '#000'; g.shadowBlur = 3;
      g.beginPath();
      g.moveTo(cx - spread - 9, cy); g.lineTo(cx - spread, cy); g.moveTo(cx + spread, cy); g.lineTo(cx + spread + 9, cy);
      g.moveTo(cx, cy - spread - 9); g.lineTo(cx, cy - spread); g.moveTo(cx, cy + spread); g.lineTo(cx, cy + spread + 9);
      g.stroke();
      g.fillStyle = '#fff'; g.fillRect(cx - 1, cy - 1, 2, 2);
    }
    g.shadowBlur = 0;
    g.font = '600 13px Rajdhani, sans-serif'; g.fillStyle = 'rgba(255,255,255,.85)';
    if (range) g.fillText(`${Math.round(range)} m${w && range > w.range_m ? ' · OUT OF RANGE' : ''}`, cx + 26, cy + 34);
    // compass strip
    const yaw = this.scene.aim.yaw;
    g.textAlign = 'center'; g.strokeStyle = 'rgba(255,255,255,.7)'; g.lineWidth = 1.2;
    for (let d = -60; d <= 60; d += 5) {
      const b = norm360(Math.round(yaw / 5) * 5 + d);
      const x = W / 2 + angleDiff(yaw, b) * 6;
      if (Math.abs(x - W / 2) > 330) continue;
      g.beginPath(); g.moveTo(x, 60); g.lineTo(x, b % 15 ? 66 : 72); g.stroke();
      if (b % 15 === 0) g.fillText(b % 90 === 0 ? 'NESW'[b / 90] : String(b), x, 88);
    }
    g.restore();
  }

  drawScope(u) {
    const g = this.ctx, W = this.canvas.width, H = this.canvas.height;
    g.clearRect(0, 0, W, H);
    const col = SENSORS[this.sensorIdx] === 'THERMAL' ? 'rgba(255,255,255,.9)' : 'rgba(120,255,170,.92)';
    g.strokeStyle = col; g.fillStyle = col; g.lineWidth = 1.5;
    g.font = '600 14px Rajdhani, sans-serif';
    let cx = W / 2, cy = H / 2;
    if (!this.scopeOn && this.aim && !this.inf) {
      const p = this.scene.project(this.aim);
      if (p) { cx = p.x; cy = p.y; }
    }
    const w = this.weapons[this.weapon];
    if (this.inf && !(this.scopeOn && this.zoomIdx >= 2)) return this.drawCrosshair(u, w, cx, cy);
    const pos = this.scene.rendered(this.unitId);
    const range = this.aim && pos ? Math.hypot(this.aim[0] - pos[0], this.aim[1] - pos[1], this.aim[2] - pos[2]) : null;
    if (this.scopeOn) {
      // vignette tube
      const r = Math.min(W, H) * 0.47;
      g.save();
      g.fillStyle = 'rgba(0,0,0,.92)';
      g.beginPath(); g.rect(0, 0, W, H); g.arc(W / 2, H / 2, r, 0, Math.PI * 2, true); g.fill();
      g.restore();
      g.beginPath(); g.arc(W / 2, H / 2, r, 0, Math.PI * 2); g.stroke();
      // main reticle with mil ladder
      const gap = 18, L = r * 0.92;
      g.beginPath();
      g.moveTo(cx - L, cy); g.lineTo(cx - gap, cy); g.moveTo(cx + gap, cy); g.lineTo(cx + L, cy);
      g.moveTo(cx, cy + gap); g.lineTo(cx, cy + L * 0.6);
      g.stroke();
      const zoom = ZOOMS[this.zoomIdx];
      const pxPerMil = (H / (2 * Math.tan((55 / zoom) * DEG / 2))) / 1000;
      for (let m = 1; m <= 10; m++) {
        const y = cy + m * pxPerMil * 2;
        if (y - cy > L * 0.6) break;
        g.beginPath(); g.moveTo(cx - (m % 5 ? 6 : 14), y); g.lineTo(cx + (m % 5 ? 6 : 14), y); g.stroke();
        if (m % 5 === 0) g.fillText(`${m * 2}`, cx + 18, y + 4);
      }
      for (let m = -10; m <= 10; m++) if (m) { const x = cx + m * pxPerMil * 4; g.beginPath(); g.moveTo(x, cy - (m % 5 ? 4 : 9)); g.lineTo(x, cy + (m % 5 ? 4 : 9)); g.stroke(); }
      g.beginPath(); g.moveTo(cx - 6, cy - 6); g.lineTo(cx, cy); g.lineTo(cx + 6, cy - 6); g.stroke();
      g.font = '700 16px Rajdhani, sans-serif';
      g.fillText(`×${zoom}  ${SENSORS[this.sensorIdx]}`, W / 2 - r * 0.62, H / 2 - r * 0.72);
      g.fillText(range ? `RNG ${Math.round(range)} m` : 'RNG ----', W / 2 + r * 0.42, H / 2 - r * 0.72);
      if (w && range && range > w.range_m) { g.fillStyle = '#ffb547'; g.fillText('OUT OF RANGE', W / 2 + r * 0.42, H / 2 - r * 0.64); g.fillStyle = col; }
    } else {
      // third-person: floating aim reticle
      g.beginPath(); g.arc(cx, cy, 14, 0, Math.PI * 2); g.stroke();
      g.beginPath(); g.moveTo(cx - 24, cy); g.lineTo(cx - 8, cy); g.moveTo(cx + 8, cy); g.lineTo(cx + 24, cy); g.moveTo(cx, cy - 24); g.lineTo(cx, cy - 8); g.moveTo(cx, cy + 8); g.lineTo(cx, cy + 24); g.stroke();
      if (range) g.fillText(`${Math.round(range)} m`, cx + 20, cy + 30);
    }
    // compass tape
    const yaw = this.scene.aim.yaw;
    g.save(); g.font = '600 13px Rajdhani, sans-serif'; g.textAlign = 'center';
    for (let d = -60; d <= 60; d += 5) {
      const b = norm360(Math.round(yaw / 5) * 5 + d);
      const x = W / 2 + angleDiff(yaw, b) * 6;
      if (Math.abs(x - W / 2) > 360) continue;
      g.beginPath(); g.moveTo(x, 60); g.lineTo(x, b % 15 ? 66 : 72); g.stroke();
      if (b % 15 === 0) g.fillText(b % 90 === 0 ? 'NESW'[b / 90] : String(b), x, 88);
    }
    g.beginPath(); g.moveTo(W / 2, 54); g.lineTo(W / 2 - 6, 46); g.lineTo(W / 2 + 6, 46); g.closePath(); g.fill();
    g.restore();
    // hull vs turret indicator
    const ix = W / 2, iy = H - 70;
    g.save(); g.translate(ix, iy);
    g.rotate(angleDiff(yaw, u.h) * DEG);
    g.strokeRect(-11, -17, 22, 34);
    g.restore();
    g.save(); g.translate(ix, iy);
    g.rotate(angleDiff(yaw, u.ty) * DEG);
    g.beginPath(); g.arc(0, 0, 7, 0, Math.PI * 2); g.stroke(); g.beginPath(); g.moveTo(0, 0); g.lineTo(0, -28); g.lineWidth = 3; g.stroke();
    g.restore();
  }
}
