// Procedural battlefield audio (Web Audio API, no sample files): engine & tracks, turret servo,
// weapon reports by type, explosions, ricochets, ambience. Distant events are delayed by the speed
// of sound, attenuated and low-passed by distance, and panned in 3D around the camera.
const SOUND_SPEED = 343;

export class BattleAudio {
  constructor() {
    this.ctx = null;
    this.enabled = true;
    this.volume = +(localStorage.getItem('wg_volume') ?? 0.8);
  }

  /** Must be called from a user gesture (browser autoplay policy). */
  unlock() {
    if (this.ctx) { if (this.ctx.state === 'suspended') this.ctx.resume(); return; }
    const C = window.AudioContext || window.webkitAudioContext;
    if (!C) return;
    const ctx = (this.ctx = new C());
    this.master = ctx.createGain();
    this.master.gain.value = this.volume;
    const comp = ctx.createDynamicsCompressor();
    comp.threshold.value = -14; comp.ratio.value = 6;
    this.master.connect(comp).connect(ctx.destination);
    // outdoor echo: short generated impulse response
    this.reverb = ctx.createConvolver();
    const len = ctx.sampleRate * 2.2, ir = ctx.createBuffer(2, len, ctx.sampleRate);
    for (let ch = 0; ch < 2; ch++) {
      const d = ir.getChannelData(ch);
      for (let i = 0; i < len; i++) d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / len, 3.2) * (i < ctx.sampleRate * 0.04 ? 0.2 : 1);
    }
    this.reverb.buffer = ir;
    this.wet = ctx.createGain(); this.wet.gain.value = 0.28;
    this.reverb.connect(this.wet).connect(this.master);
    this.noise = ctx.createBuffer(1, ctx.sampleRate * 2, ctx.sampleRate);
    const nd = this.noise.getChannelData(0);
    for (let i = 0; i < nd.length; i++) nd[i] = Math.random() * 2 - 1;
    this.listener = ctx.listener;
    this.startAmbience();
  }
  setVolume(v) { this.volume = v; localStorage.setItem('wg_volume', v); if (this.master) this.master.gain.value = v; }

  /** Camera position/orientation (three.js world) for 3D panning. */
  setListener(pos, fwd) {
    if (!this.ctx) return;
    const l = this.listener, t = this.ctx.currentTime;
    if (l.positionX) {
      l.positionX.setValueAtTime(pos.x, t); l.positionY.setValueAtTime(pos.y, t); l.positionZ.setValueAtTime(pos.z, t);
      l.forwardX.setValueAtTime(fwd.x, t); l.forwardY.setValueAtTime(fwd.y, t); l.forwardZ.setValueAtTime(fwd.z, t);
      l.upX.setValueAtTime(0, t); l.upY.setValueAtTime(1, t); l.upZ.setValueAtTime(0, t);
    } else { l.setPosition(pos.x, pos.y, pos.z); l.setOrientation(fwd.x, fwd.y, fwd.z, 0, 1, 0); }
    this.listenerPos = pos;
  }

  // ── building blocks ───────────────────────────────────────────────────────
  noiseSrc() { const s = this.ctx.createBufferSource(); s.buffer = this.noise; s.loop = true; s.loopStart = Math.random(); return s; }
  /** Output chain for a world-space event: distance delay + air absorption + 3D pan + reverb send. */
  chain(pos, gain, mine) {
    const ctx = this.ctx;
    const g = ctx.createGain(); g.gain.value = gain;
    let delay = 0, dist = 0;
    const out = ctx.createGain();
    if (pos && this.listenerPos && !mine) {
      dist = Math.hypot(pos.x - this.listenerPos.x, pos.y - this.listenerPos.y, pos.z - this.listenerPos.z);
      delay = Math.min(12, dist / SOUND_SPEED);
      const air = ctx.createBiquadFilter(); air.type = 'lowpass'; air.frequency.value = Math.max(300, 16000 / (1 + dist / 400));
      const pan = ctx.createPanner();
      pan.panningModel = 'equalpower'; pan.distanceModel = 'inverse'; pan.refDistance = 40; pan.rolloffFactor = 1.1;
      pan.positionX ? (pan.positionX.value = pos.x, pan.positionY.value = pos.y, pan.positionZ.value = pos.z) : pan.setPosition(pos.x, pos.y, pos.z);
      g.connect(air).connect(pan).connect(out);
    } else g.connect(out);
    out.connect(this.master);
    const send = ctx.createGain(); send.gain.value = Math.min(1, 0.3 + dist / 2500);
    out.connect(send).connect(this.reverb);
    return { input: g, t0: ctx.currentTime + delay, dist };
  }
  burst(ch, { f0 = 800, f1 = 120, q = 0.8, dur = 0.5, type = 'lowpass', amp = 1, attack = 0.002 }) {
    const ctx = this.ctx, s = this.noiseSrc(), f = ctx.createBiquadFilter(), e = ctx.createGain();
    f.type = type; f.Q.value = q;
    f.frequency.setValueAtTime(f0, ch.t0); f.frequency.exponentialRampToValueAtTime(Math.max(20, f1), ch.t0 + dur);
    e.gain.setValueAtTime(0.0001, ch.t0); e.gain.exponentialRampToValueAtTime(amp, ch.t0 + attack); e.gain.exponentialRampToValueAtTime(0.0001, ch.t0 + dur);
    s.connect(f).connect(e).connect(ch.input);
    s.start(ch.t0); s.stop(ch.t0 + dur + 0.05);
  }
  tone(ch, { f0 = 60, f1 = 30, dur = 0.4, type = 'sine', amp = 1 }) {
    const ctx = this.ctx, o = ctx.createOscillator(), e = ctx.createGain();
    o.type = type;
    o.frequency.setValueAtTime(f0, ch.t0); o.frequency.exponentialRampToValueAtTime(Math.max(10, f1), ch.t0 + dur);
    e.gain.setValueAtTime(0.0001, ch.t0); e.gain.exponentialRampToValueAtTime(amp, ch.t0 + 0.004); e.gain.exponentialRampToValueAtTime(0.0001, ch.t0 + dur);
    o.connect(e).connect(ch.input);
    o.start(ch.t0); o.stop(ch.t0 + dur + 0.05);
  }

  // ── events ────────────────────────────────────────────────────────────────
  shot(fx, weapon, pos, mine) {
    if (!this.ctx || !this.enabled) return;
    const ch = this.chain(pos, mine ? 1.0 : 0.9, mine);
    if (ch.dist > 9000) return;
    if (fx === 'RAIL') {
      this.tone(ch, { f0: 3200, f1: 180, dur: 0.35, type: 'sawtooth', amp: 0.35 });
      this.burst(ch, { f0: 9000, f1: 600, dur: 0.25, type: 'highpass', amp: 0.7 });
      this.tone(ch, { f0: 70, f1: 28, dur: 0.6, amp: 1 });
    } else if (fx === 'TRACER' && /pistol|smg|rifle-m4|rifle-ak|dmr|sniper|shotgun/.test(weapon || '')) { // small arms: one report
      const big = /sniper|dmr/.test(weapon), shot = /shotgun/.test(weapon), soft = /pistol|smg/.test(weapon);
      this.burst(ch, { f0: soft ? 5200 : 4200, f1: soft ? 900 : 500, dur: big ? 0.35 : shot ? 0.25 : 0.1, type: 'highpass', amp: big ? 0.8 : soft ? 0.38 : 0.55 });
      this.tone(ch, { f0: big ? 120 : shot ? 90 : 200, f1: big ? 40 : 60, dur: big ? 0.4 : shot ? 0.25 : 0.08, amp: big ? 0.6 : shot ? 0.5 : 0.28 });
    } else if (fx === 'TRACER' && /rifle/.test(weapon || '')) { // assault rifle: 3-round burst
      for (let i = 0; i < 3; i++) { const c2 = { ...ch, t0: ch.t0 + i * 0.1 }; this.burst(c2, { f0: 6000, f1: 900, dur: 0.07, type: 'highpass', amp: 0.45 }); this.tone(c2, { f0: 220, f1: 90, dur: 0.06, amp: 0.25 }); }
    } else if (fx === 'TRACER' && /hmg|ac-/.test(weapon || '')) {
      for (let i = 0; i < 3; i++) { const c2 = { ...ch, t0: ch.t0 + i * 0.09 }; this.burst(c2, { f0: 3500, f1: 400, dur: 0.12, amp: 0.55 }); this.tone(c2, { f0: 140, f1: 60, dur: 0.1, amp: 0.4 }); }
    } else if (fx === 'TRACER') { // tank main gun
      this.burst(ch, { f0: 2400, f1: 80, dur: 0.9, amp: 1 });
      this.tone(ch, { f0: 55, f1: 22, dur: 0.9, amp: 1 });
    } else if (fx === 'MISSILE') {
      this.burst(ch, { f0: 900, f1: 2600, dur: 1.4, type: 'bandpass', q: 1.5, amp: 0.55, attack: 0.05 });
      this.tone(ch, { f0: 90, f1: 40, dur: 0.4, amp: 0.5 });
    } else if (fx === 'SHELL' || fx === 'ROCKET') {
      this.burst(ch, { f0: 1800, f1: 60, dur: 1.4, amp: 1 });
      this.tone(ch, { f0: 45, f1: 18, dur: 1.2, amp: 1 });
    } else if (fx === 'BEAM') {
      this.tone(ch, { f0: 1600, f1: 1550, dur: 0.3, type: 'square', amp: 0.08 });
    }
  }
  explosion(pos, size = 1) {
    if (!this.ctx || !this.enabled) return;
    const ch = this.chain(pos, Math.min(1.4, 0.8 * size), false);
    if (ch.dist > 12000) return;
    this.burst(ch, { f0: 1600, f1: 40, dur: 1.6 + size * 0.6, amp: 1 });
    this.tone(ch, { f0: 48, f1: 16, dur: 1.4 + size * 0.4, amp: 1 });
    this.burst({ ...ch, t0: ch.t0 + 0.12 }, { f0: 500, f1: 60, dur: 2.4, amp: 0.4 }); // debris rumble
  }
  impact(pos, kind) {
    if (!this.ctx || !this.enabled) return;
    const ch = this.chain(pos, 0.7, false);
    if (ch.dist > 5000) return;
    if (kind === 'NO_PEN') { this.tone(ch, { f0: 1700, f1: 900, dur: 0.6, type: 'triangle', amp: 0.4 }); this.burst(ch, { f0: 5000, f1: 1500, dur: 0.2, type: 'bandpass', amp: 0.5 }); }
    else this.burst(ch, { f0: 900, f1: 120, dur: 0.5, amp: 0.45 }); // dirt thump
  }
  /** Incoming round passing close: supersonic crack. */
  crack() {
    if (!this.ctx) return;
    const ch = this.chain(null, 0.6, true);
    this.burst(ch, { f0: 7000, f1: 2500, dur: 0.07, type: 'highpass', amp: 0.8 });
  }
  hitOwn() {
    if (!this.ctx) return;
    const ch = this.chain(null, 1, true);
    this.tone(ch, { f0: 900, f1: 300, dur: 0.5, type: 'triangle', amp: 0.6 });
    this.burst(ch, { f0: 3000, f1: 200, dur: 0.6, amp: 0.9 });
    this.tone(ch, { f0: 70, f1: 30, dur: 0.6, amp: 0.9 });
  }
  ui(kind) { // hit marker / kill confirm ticks
    if (!this.ctx) return;
    const ch = this.chain(null, 0.35, true);
    if (kind === 'kill') { this.tone(ch, { f0: 880, f1: 880, dur: 0.12, type: 'square', amp: 0.25 }); this.tone({ ...ch, t0: ch.t0 + 0.12 }, { f0: 1320, f1: 1320, dur: 0.18, type: 'square', amp: 0.25 }); }
    else if (kind === 'pick') { this.tone(ch, { f0: 420, f1: 300, dur: 0.07, type: 'triangle', amp: 0.2 }); this.tone({ ...ch, t0: ch.t0 + 0.06 }, { f0: 600, f1: 500, dur: 0.06, type: 'triangle', amp: 0.16 }); }
    else if (kind === 'reload') { for (let i = 0; i < 2; i++) this.burst({ ...ch, t0: ch.t0 + i * 0.35 }, { f0: 3000, f1: 1500, dur: 0.05, type: 'bandpass', q: 3, amp: 0.3 }); }
    else if (kind === 'chat') this.tone(ch, { f0: 700, f1: 760, dur: 0.08, type: 'sine', amp: 0.12 });
    else this.tone(ch, { f0: 1500, f1: 1400, dur: 0.06, type: 'square', amp: 0.18 });
  }
  /** Own footstep (soldier): soft crunch, tuned by ground. */
  step(ground, run) {
    if (!this.ctx || !this.enabled) return;
    const ch = this.chain(null, run ? 0.32 : 0.22, true);
    const soft = ground === 'SAND' || ground === 'SNOW' || ground === 'MARSH' || ground === 'MUD';
    if (ground === 'WATER') this.burst(ch, { f0: 1400, f1: 300, dur: 0.25, type: 'bandpass', q: 0.8, amp: 0.4 });
    else this.burst(ch, { f0: soft ? 700 : 2200, f1: soft ? 150 : 400, dur: soft ? 0.12 : 0.06, amp: 0.5 });
  }

  // ── continuous: own vehicle engine, tracks, servo; ambience ───────────────
  startEngine(kind) {
    if (!this.ctx || this.engine) return;
    const ctx = this.ctx;
    const g = ctx.createGain(); g.gain.value = 0;
    const lp = ctx.createBiquadFilter(); lp.type = 'lowpass'; lp.frequency.value = 400;
    const o1 = ctx.createOscillator(), o2 = ctx.createOscillator();
    o1.type = 'sawtooth'; o2.type = 'square';
    o1.frequency.value = 38; o2.frequency.value = 19;
    const g2 = ctx.createGain(); g2.gain.value = 0.35;
    o1.connect(lp); o2.connect(g2).connect(lp);
    // track clatter / hover whine
    const n = this.noiseSrc(), bp = ctx.createBiquadFilter(), ng = ctx.createGain(), am = ctx.createGain(), lfo = ctx.createOscillator(), lg = ctx.createGain();
    bp.type = 'bandpass'; bp.frequency.value = kind === 'hover' ? 2200 : 900; bp.Q.value = 1.2;
    ng.gain.value = 0;
    lfo.frequency.value = 8; lg.gain.value = 0.5; lfo.connect(lg).connect(am.gain); am.gain.value = 0.5;
    n.connect(bp).connect(am).connect(ng).connect(g);
    lp.connect(g);
    // turret servo
    const so = ctx.createOscillator(), sg = ctx.createGain();
    so.type = 'triangle'; so.frequency.value = 420; sg.gain.value = 0;
    so.connect(sg).connect(this.master);
    g.connect(this.master);
    [o1, o2, n, lfo, so].forEach((x) => x.start());
    this.engine = { g, lp, o1, o2, ng, lfo, so, sg, nodes: [o1, o2, n, lfo, so] };
  }
  updateEngine(speed, throttle, servoRate) {
    const e = this.engine;
    if (!e) return;
    const t = this.ctx.currentTime, v = Math.abs(speed);
    const rpm = 0.25 + Math.min(1, Math.abs(throttle) * 0.6 + v / 25 * 0.5);
    e.o1.frequency.setTargetAtTime(32 + rpm * 55, t, 0.15);
    e.o2.frequency.setTargetAtTime(16 + rpm * 27, t, 0.15);
    e.lp.frequency.setTargetAtTime(250 + rpm * 900, t, 0.2);
    e.g.gain.setTargetAtTime(0.18 + rpm * 0.32, t, 0.2);
    e.ng.gain.setTargetAtTime(Math.min(0.5, v / 18), t, 0.2);
    e.lfo.frequency.setTargetAtTime(2 + v * 1.1, t, 0.2);
    e.sg.gain.setTargetAtTime(Math.min(0.06, servoRate / 600), t, 0.05);
    e.so.frequency.setTargetAtTime(380 + Math.min(300, servoRate * 3), t, 0.05);
  }
  stopEngine() {
    const e = this.engine;
    if (!e) return;
    const t = this.ctx.currentTime;
    e.g.gain.setTargetAtTime(0, t, 0.2); e.sg.gain.setTargetAtTime(0, t, 0.05);
    setTimeout(() => e.nodes.forEach((n) => { try { n.stop(); } catch (_) { /* stopped */ } }), 800);
    this.engine = null;
  }
  startAmbience() {
    const ctx = this.ctx;
    const n = this.noiseSrc(), bp = ctx.createBiquadFilter(), g = ctx.createGain(), lfo = ctx.createOscillator(), lg = ctx.createGain();
    bp.type = 'bandpass'; bp.frequency.value = 500; bp.Q.value = 0.6;
    g.gain.value = 0.05; lfo.frequency.value = 0.12; lg.gain.value = 0.035;
    lfo.connect(lg).connect(g.gain);
    n.connect(bp).connect(g).connect(this.master);
    n.start(); lfo.start();
    this.wind = g;
    const r = this.noiseSrc(), hp = ctx.createBiquadFilter(), rg = ctx.createGain();
    hp.type = 'highpass'; hp.frequency.value = 2500; rg.gain.value = 0;
    r.connect(hp).connect(rg).connect(this.master); r.start();
    this.rainGain = rg;
  }
  setWeather(w) {
    if (!this.ctx) { this.pendingWeather = w; return; }
    const t = this.ctx.currentTime;
    this.rainGain.gain.setTargetAtTime(/STORM/.test(w) ? 0.18 : /RAIN/.test(w) ? 0.1 : 0, t, 1);
    this.wind.gain.setTargetAtTime(/BLIZZARD|DUST|STORM/.test(w) ? 0.16 : 0.05, t, 1);
  }
}

export const audio = new BattleAudio();
