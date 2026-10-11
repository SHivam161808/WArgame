// Battle page controller — players (/play/<room>/) and the Exercise Controller's live monitor.
import { api, esc, Socket, toast } from './api.js';
import { centroid, compass, gridRef, pointInPolygon } from './geo.js';
import { clearLayer, createGlobe, drawCorridors, drawZones, home, pickLocal, search, setImagery, setLayer, toggle2D } from './globe.js';
import { DriveController } from './hud.js';
import { BattleScene, TEAM } from './scene.js';
import { GroundView } from './ground/view.js';
import { audio } from './ground/audio.js';

const C = Cesium;
const $ = (s) => document.querySelector(s);
const app = $('#app');
const MODE = app.dataset.mode; // 'player' | 'admin'
const ROOM = app.dataset.room;
const FORMS = ['ALPHA', 'BRAVO', 'CHARLIE', 'DELTA'];
const ROLE = { ALPHA: 'Assault', BRAVO: 'HVT Defence', CHARLIE: 'Fire Support', DELTA: 'Reconnaissance' };
const DOCTRINES = ['AGGRESSIVE', 'FLANKING', 'SUPPRESSIVE', 'DEFENSIVE', 'RECON', 'HOLD'];
const SHAPES = ['WEDGE', 'LINE', 'COLUMN', 'ECHELON_L', 'ECHELON_R', 'VEE', 'RING', 'DIAMOND'];
const CLS_ICON = { MBT: 'bi-shield-fill', IFV: 'bi-shield-shaded', APC: 'bi-truck', SPG: 'bi-bullseye', MLRS: 'bi-grid-3x3', RECON: 'bi-binoculars-fill',
  HOVER: 'bi-wind', GUNSHIP: 'bi-fan', JET: 'bi-airplane-fill', UAV: 'bi-send-fill', AD: 'bi-radar', HVT: 'bi-star-fill' };

window.__S = undefined;
const S = {
  session: null, lobby: null, frame: null, me: null, side: null, g: null, scene: null, drive: null,
  history: new Map(), liveTick: 0, scrub: false, selected: null, mode: null, routeDraft: [], plan: null, placing: null,
  lastUi: 0, feed: [], contacts: new Set(), token: null, ended: false, deployMarks: [],
};
window.__S = S;
const tokenKey = `wg_token_${ROOM}`;
if (MODE === 'admin') document.querySelectorAll('.admin-only').forEach((e) => (e.style.display = ''));
else document.querySelectorAll('.admin-only').forEach((e) => (e.style.display = 'none'));

// ── connect ────────────────────────────────────────────────────────────────
let sock;
function connect() {
  sock = new Socket(ROOM, async () => {
    try {
      if (MODE === 'admin') await sock.request('admin_watch');
      else {
        const r = await sock.request('join', { token: S.token });
        S.me = r.player_id; S.side = r.side;
        if (S.drive) S.drive.playerId = S.me;
      }
    } catch (e) {
      if (MODE === 'player') { localStorage.removeItem(tokenKey); S.token = null; sock.close(); showJoin(e.message); }
      else toast(e.message, 'crit', 10000);
    }
  });
  sock.on('session', (m) => { if (m.player_id) S.me = m.player_id; onSession(m.session); });
  sock.on('lobby', (m) => onLobby(m.lobby));
  sock.on('frame', (m) => onFrame(m.frame));
  sock.on('advisor', (m) => renderAdvisor(m.assessment));
  sock.on('ended', (m) => onEnded(m.result, m.battle_id));
  sock.on('rerun', () => { S.ended = false; S.history.clear(); allScenes().forEach((x) => x.reset()); S.feed = []; renderFeed(); bootstrap.Modal.getInstance($('#result-modal'))?.hide(); toast('New run: same opening positions', 'info'); });
  sock.on('toast', (m) => toast(m.text, m.level));
  sock.on('chat', (m) => addChat(m));
  sock.on('_close', () => setBanner('Connection lost — reconnecting…'));
  sock.on('_open', () => setBanner(null));
}

function showJoin(err) {
  const m = new bootstrap.Modal($('#join-modal'));
  $('#j-room').value = ROOM;
  $('#j-call').value = localStorage.getItem('wg_callsign') || '';
  $('#j-err').textContent = err || '';
  m.show();
  $('#join-form').onsubmit = async (e) => {
    e.preventDefault();
    try {
      const r = await api('join/', { method: 'POST', body: { room_code: $('#j-room').value, force_key: $('#j-key').value, callsign: $('#j-call').value } });
      localStorage.setItem('wg_callsign', $('#j-call').value);
      if (r.room_code !== ROOM) { localStorage.setItem(`wg_token_${r.room_code}`, r.token); location.href = `/play/${r.room_code}/`; return; }
      localStorage.setItem(tokenKey, r.token);
      S.token = r.token;
      m.hide();
      connect();
    } catch (ex) { $('#j-err').textContent = ex.message; }
  };
}

if (MODE === 'player') {
  S.token = localStorage.getItem(tokenKey) || new URLSearchParams(location.search).get('token');
  if (S.token) { localStorage.setItem(tokenKey, S.token); connect(); } else showJoin();
} else connect();

// ── session / globe ───────────────────────────────────────────────────────
async function onSession(sess) {
  const first = !S.session;
  S.session = sess;
  S.side = MODE === 'admin' ? null : sess.side;
  if (!first) return;
  const sc = sess.scenario;
  S.g = await createGlobe({ container: 'globe', origin: sc.origin, halfKm: sc.ao_half_km, theatre: sess.theatre,
    flat: sess.terrain?.meta?.source === 'flat', waterLevel: sess.terrain?.meta?.water_level ?? null, lowPower: /low=1/.test(location.search) });
  drawZones(S.g, sess.zones);
  drawCorridors(S.g, sess.corridors || []);
  const onSelect = (id) => { if (S.selected === id) return; S.selected = id; allScenes().forEach((x) => x.selected !== id && x.select(id)); renderSelection(); renderRoster(true); };
  S.scene = new BattleScene(S.g, sess, { side: S.side, onSelect });
  S.drive = null;
  setBanner('Loading 3D battlefield…');
  try {
    const gq = { high: 1, medium: 0.75, low: 0.5 }[localStorage.getItem('wg_quality') || 'high'] ?? 1;
    S.ground = await GroundView.create($('#ground'), sess, { side: S.side, onSelect, quality: /low=1/.test(location.search) ? 0.5 : gq });
    S.drive = new DriveController({ scene: S.ground, socket: sock, session: sess, el: $('#hud'), onExit: () => renderSelection() });
    S.drive.playerId = S.me;
    wireGround();
  } catch (e) { console.error(e); toast('3D battle view failed to load — map view only', 'warn'); }
  setBanner(null);
  setView(S.lobby && S.lobby.status !== 'DEPLOYMENT' && S.ground ? 'ground' : 'map');
  wireMap();
  wireUi();
  wireChat();
  $('#me-badge').textContent = MODE === 'admin' ? 'CONTROLLER' : `${S.side}`;
  $('#me-badge').className = `ms-auto badge ${S.side === 'RED' ? 'badge-red' : 'badge-blue'}`;
  $('#s-force').textContent = MODE === 'admin' ? 'ADMIN' : S.side;
  $('#s-force').className = `v ${S.side === 'RED' ? 'side-red' : 'side-blue'}`;
  if (S.lobby) onLobby(S.lobby);
  if (S.frame) allScenes().forEach((x) => x.ingest(S.frame, { instant: true }));
  setInterval(() => { $('#fps').textContent = S.g ? `${Math.round(S.g.fps)} fps` : ''; }, 2000);
}

// ── lobby & deployment (BP-1, BP-2, PL-4) ─────────────────────────────────
function amCommander() { return MODE === 'admin' || (S.lobby && S.lobby.commanders[S.side] === S.me); }
function mySlot() { return S.lobby?.slots.find((s) => s.player_id === S.me)?.slot; }
function canEdit(fid) { return MODE === 'admin' || amCommander() || mySlot() === fid; }
function planSide() { return MODE === 'admin' ? (S.adminPlanSide || 'BLUE') : S.side; }

function onLobby(lb) {
  const prevStatus = S.lobby?.status;
  S.lobby = lb;
  const p = lb.plans[planSide()];
  if (p && !S.placing) S.plan = structuredClone(p);
  updateTop();
  updatePace();
  $('#l-fog').checked = lb.fog_enabled;
  if (lb.status === 'DEPLOYMENT') { renderLobby(); $('#lobby').style.display = S.placing ? 'none' : ''; renderDeployMarks(); }
  else { $('#lobby').style.display = 'none'; clearDeployMarks(); }
  if (prevStatus === 'DEPLOYMENT' && lb.status === 'RUNNING') { toast('Battle started — M toggles 3D view / map', 'ok'); home(S.g, 1.2); if (S.ground) setView('ground'); }
}

function renderLobby() {
  const lb = S.lobby, sess = S.session;
  if (!sess) return;
  const sc = sess.scenario;
  $('#lb-room').textContent = ROOM;
  $('#lb-title').textContent = sc.name;
  $('#lb-sub').textContent = `${sess.theatre.name} · ${sess.theatre.season} · ${sc.weather} · ${sc.time_of_day} · RED: ${lb.red_mode === 'AI' ? 'AI Red player' : lb.red_mode} · run #${lb.run_no}`;
  $('#lb-brief').textContent = lb.briefing || sess.briefing || '';
  for (const side of ['BLUE', 'RED']) {
    const el = $(side === 'BLUE' ? '#lb-ready-b' : '#lb-ready-r');
    el.textContent = `${side}: ${lb.ready[side] ? 'READY' : 'not ready'}`;
    el.className = `badge ${lb.ready[side] ? 'badge-green' : 'badge-amber'}`;
  }
  const slotSides = MODE === 'admin' ? ['BLUE', 'RED'] : [S.side];
  $('#lb-slots').innerHTML = slotSides.map((side) => lb.slots.filter((s) => s.side === side).map((s) => {
    const mine = s.player_id === S.me;
    const who = s.callsign ? `${esc(s.callsign)}${s.commander ? ' <span class="badge badge-amber">CMDR</span>' : ''}${s.connected ? '' : ' <span class="text-muted2">(offline → AI)</span>'}` : '<span class="text-muted2"><i class="bi bi-cpu"></i> AI commander</span>';
    const btn = MODE === 'player' && !s.player_id ? `<button class="btn btn-sm btn-tac mt-2 w-100" data-claim="${s.slot}">Take ${s.slot}</button>` : '';
    return `<div class="col-md-3"><div class="slot-card ${mine ? 'mine' : ''}"><div class="hud-font fw-bold ${side === 'RED' ? 'side-red' : 'side-blue'}">${side} ${s.slot}</div>
      <div class="small text-muted2">${s.role}</div><div class="small mt-1">${who}</div>${btn}</div></div>`;
  }).join('')).join('');
  $('#lb-slots').querySelectorAll('[data-claim]').forEach((b) => (b.onclick = () => sock.request('claim_slot', { slot: b.dataset.claim }).catch((e) => toast(e.message, 'warn'))));
  const sk = sess.scenario.mode === 'SKIRMISH';
  if (sk) renderSkirmishLobby(lb); else renderBuilder();
  const cmd = amCommander();
  const side = planSide();
  $('#lb-ready').style.display = MODE === 'admin' || cmd ? '' : 'none';
  $('#lb-ready').innerHTML = lb.ready[side] ? '<i class="bi bi-x-circle"></i> CANCEL READY' : `<i class="bi bi-check2-circle"></i> ${side} READY`;
  $('#lb-ready').className = `btn btn-lg ${lb.ready[side] ? 'btn-outline-warning' : 'btn-success'} hud-font px-4`;
  $('#lb-hvt').disabled = !cmd;
  for (const id of ['#lb-hvt', '#lb-suggest', '#lb-builder-panel']) $(id).style.display = sk ? 'none' : '';
}

function renderSkirmishLobby(lb) {
  $('#lb-slots-title').textContent = 'Squads — up to 4 players a side; every empty place is filled by a bot';
  const sides = MODE === 'admin' ? ['BLUE', 'RED'] : [S.side];
  $('#lb-slots').innerHTML = sides.map((side) => {
    const ps = lb.players.filter((p) => p.side === side);
    const rows = [0, 1, 2, 3].map((i) => {
      const p = ps[i];
      return `<div class="col-md-3"><div class="slot-card ${p && p.id === S.me ? 'mine' : ''}"><div class="hud-font fw-bold ${side === 'RED' ? 'side-red' : 'side-blue'}">${side} ${i + 1}</div>
        <div class="small mt-1">${p ? `${esc(p.callsign)}${lb.commanders[side] === p.id ? ' <span class="badge badge-amber">LEADER</span>' : ''}${p.connected ? '' : ' <span class="text-muted2">(offline)</span>'}` : '<span class="text-muted2"><i class="bi bi-cpu"></i> bot</span>'}</div></div></div>`;
    }).join('');
    return rows;
  }).join('');
  $('#lb-howto').innerHTML = `<ol class="ps-3 mb-0">
    <li>Share the Room ID and your side's key — friends join BLUE or RED (up to 4 a side).</li>
    <li>The squad leader presses <b>READY</b>; RED readies itself when it is all bots.</li>
    <li>You start with a pistol: search the houses and the school for rifles, SMGs, shotguns, sniper rifles, RPGs, ammo, medkits, vests and helmets (<b>F</b> to pick up).</li>
    <li><b>F</b> next to a car, bike, jeep or tank to drive it. <b>R</b> reload · <b>H</b> medkit · <b>M</b> map · <b>Enter</b> team chat.</li>
    <li>No respawns — the last squad standing wins.</li></ol>`;
}

function inventoryFor(side) { const inv = S.session.inventory; return MODE === 'admin' ? inv[side] : inv; }
function platformsFor(side) {
  const inv = inventoryFor(side);
  const all = Object.values(S.session.catalogue.platforms).filter((p) => p.cls !== 'HVT');
  return inv ? all.filter((p) => inv[p.id] > 0) : all.filter((p) => p.faction === side || p.faction === 'ANY');
}
function used(pid) { return (S.plan?.formations || []).reduce((s, f) => s + (f.composition[pid] || 0), 0); }

function renderBuilder() {
  const side = planSide();
  if (!S.plan) S.plan = { side, formations: [], hvt: null };
  const inv = inventoryFor(side);
  const plats = platformsFor(side);
  $('#lb-inv').textContent = inv ? 'Inventory limits set by the Exercise Controller' : 'Unrestricted order of battle';
  const byId = Object.fromEntries((S.plan.formations || []).map((f) => [f.id, f]));
  const zone = S.session.zones.find((z) => z.side === side)?.polygon || [];
  $('#lb-forms').innerHTML = FORMS.map((fid) => {
    const f = byId[fid] || { id: fid, name: `${fid} (${ROLE[fid]})`, doctrine: 'AGGRESSIVE', shape: 'WEDGE', composition: {}, anchor: null };
    const ed = canEdit(fid);
    const n = Object.values(f.composition).reduce((a, b) => a + b, 0);
    const rows = plats.map((p) => {
      const c = f.composition[p.id] || 0;
      const left = inv ? inv[p.id] - used(p.id) : 99;
      return `<div class="comp-row"><span title="${esc(p.description)}"><i class="bi ${CLS_ICON[p.cls]}"></i> ${esc(p.name)}</span>
        <span class="qty">${ed ? `<button class="btn btn-sm btn-outline-secondary" data-f="${fid}" data-p="${p.id}" data-d="-1">−</button>` : ''}<span>${c}</span>${ed ? `<button class="btn btn-sm btn-outline-secondary" data-f="${fid}" data-p="${p.id}" data-d="1" ${left <= 0 ? 'disabled' : ''}>+</button>` : ''}
        ${inv ? `<small class="text-muted2">/${left + c}</small>` : ''}</span></div>`;
    }).join('');
    const where = f.anchor ? `grid ${gridRef(f.anchor[0], f.anchor[1], S.session.scenario.ao_half_km * 1000)}` : 'not placed';
    return `<div class="col-md-6"><div class="slot-card">
      <div class="d-flex align-items-center gap-2 mb-2"><span class="hud-font fw-bold">${fid}</span><span class="small text-muted2">${ROLE[fid]} · ${n} platforms</span>
        ${ed ? `<button class="btn btn-sm btn-tac ms-auto" data-place="${fid}"><i class="bi bi-geo-alt"></i> ${f.anchor ? 'Move' : 'Place'}</button>` : ''}</div>
      <div class="row g-1 mb-2"><div class="col-6"><select class="form-select form-select-sm" data-doc="${fid}" ${ed ? '' : 'disabled'}>${DOCTRINES.map((d) => `<option ${d === f.doctrine ? 'selected' : ''}>${d}</option>`).join('')}</select></div>
        <div class="col-6"><select class="form-select form-select-sm" data-shape="${fid}" ${ed ? '' : 'disabled'}>${SHAPES.map((d) => `<option ${d === f.shape ? 'selected' : ''}>${d}</option>`).join('')}</select></div></div>
      <div class="small mb-1 ${f.anchor ? 'text-success' : 'text-warning'}"><i class="bi bi-pin-map"></i> ${where}</div>
      <div class="scroll" style="max-height:190px">${rows}</div></div></div>`;
  }).join('') + `<div class="col-12 small ${S.plan.hvt ? 'text-success' : 'text-warning'}"><i class="bi bi-star-fill"></i> HVT: ${S.plan.hvt ? `grid ${gridRef(S.plan.hvt[0], S.plan.hvt[1], S.session.scenario.ao_half_km * 1000)}` : 'not placed — the Commander must place it inside the zone'}</div>`;
  const edit = (fid, fn) => {
    let f = S.plan.formations.find((x) => x.id === fid);
    if (!f) { f = { id: fid, name: `${fid} (${ROLE[fid]})`, doctrine: 'AGGRESSIVE', shape: 'WEDGE', composition: {}, anchor: zone.length ? centroid(zone) : [0, 0] }; S.plan.formations.push(f); }
    fn(f);
    pushPlan();
  };
  $('#lb-forms').querySelectorAll('[data-d]').forEach((b) => (b.onclick = () => edit(b.dataset.f, (f) => { f.composition[b.dataset.p] = Math.max(0, (f.composition[b.dataset.p] || 0) + +b.dataset.d); })));
  $('#lb-forms').querySelectorAll('[data-doc]').forEach((s) => (s.onchange = () => edit(s.dataset.doc, (f) => (f.doctrine = s.value))));
  $('#lb-forms').querySelectorAll('[data-shape]').forEach((s) => (s.onchange = () => edit(s.dataset.shape, (f) => (f.shape = s.value))));
  $('#lb-forms').querySelectorAll('[data-place]').forEach((b) => (b.onclick = () => startPlacing(b.dataset.place)));
}

let pushTimer;
function pushPlan() {
  renderBuilder();
  renderDeployMarks();
  clearTimeout(pushTimer);
  pushTimer = setTimeout(async () => {
    try {
      if (MODE === 'admin') await sock.request('deploy_plan', { side: planSide(), plan: S.plan });
      else await sock.request('deploy_plan', { plan: S.plan });
    } catch (e) { toast(e.message, 'warn'); }
  }, 250);
}

function startPlacing(what) {
  S.placing = what;
  $('#lobby').style.display = 'none';
  setBanner(what === 'HVT' ? `Click inside the ${planSide()} zone to place the HVT · Esc to cancel` : `Click inside the ${planSide()} zone to place ${what} · Esc to cancel`);
  home(S.g, 0.8);
}
function finishPlacing(pt) {
  const zone = S.session.zones.find((z) => z.side === planSide())?.polygon || [];
  if (pt && zone.length && !pointInPolygon(pt[0], pt[1], zone)) { toast('Must be inside your zone', 'warn'); return; }
  const what = S.placing;
  S.placing = null;
  setBanner(null);
  $('#lobby').style.display = '';
  if (!pt) return;
  if (what === 'HVT') S.plan.hvt = [pt[0], pt[1]];
  else {
    let f = S.plan.formations.find((x) => x.id === what);
    if (!f) { f = { id: what, name: `${what} (${ROLE[what]})`, doctrine: 'AGGRESSIVE', shape: 'WEDGE', composition: {} }; S.plan.formations.push(f); }
    f.anchor = [pt[0], pt[1]];
    const ez = S.session.zones.find((z) => z.side !== planSide());
    f.facing = ez ? Math.round((Math.atan2(centroid(ez.polygon)[0] - pt[0], centroid(ez.polygon)[1] - pt[1]) * 180) / Math.PI + 360) % 360 : null;
  }
  pushPlan();
}
function clearDeployMarks() { for (const e of S.deployMarks) S.g?.viewer.entities.remove(e); S.deployMarks = []; }
function renderDeployMarks() {
  if (!S.g || !S.plan) return;
  clearDeployMarks();
  const col = TEAM[planSide()];
  for (const f of S.plan.formations) {
    if (!f.anchor) continue;
    S.deployMarks.push(S.g.viewer.entities.add({ position: S.g.frame.cart(f.anchor[0], f.anchor[1]), point: { pixelSize: 12, color: col, outlineColor: C.Color.WHITE, outlineWidth: 2, heightReference: C.HeightReference.CLAMP_TO_GROUND, disableDepthTestDistance: Infinity },
      label: { text: f.id, font: '700 15px Rajdhani', fillColor: col, outlineColor: C.Color.BLACK, outlineWidth: 3, style: C.LabelStyle.FILL_AND_OUTLINE, pixelOffset: new C.Cartesian2(0, -18), heightReference: C.HeightReference.CLAMP_TO_GROUND, disableDepthTestDistance: Infinity } }));
  }
  if (S.plan.hvt) S.deployMarks.push(S.g.viewer.entities.add({ position: S.g.frame.cart(S.plan.hvt[0], S.plan.hvt[1]), point: { pixelSize: 16, color: C.Color.GOLD, outlineColor: col, outlineWidth: 3, heightReference: C.HeightReference.CLAMP_TO_GROUND, disableDepthTestDistance: Infinity },
    label: { text: 'HVT', font: '700 15px Rajdhani', fillColor: C.Color.GOLD, outlineColor: C.Color.BLACK, outlineWidth: 3, style: C.LabelStyle.FILL_AND_OUTLINE, pixelOffset: new C.Cartesian2(0, -20), heightReference: C.HeightReference.CLAMP_TO_GROUND, disableDepthTestDistance: Infinity } }));
}

// ── frames ─────────────────────────────────────────────────────────────────
function onFrame(fr) {
  S.frame = fr;
  S.liveTick = Math.max(S.liveTick, fr.tick);
  S.history.set(fr.tick, fr);
  if (S.history.size > 1500) S.history.delete(S.history.keys().next().value);
  if (!S.scene) return;
  if (!S.scrub) allScenes().forEach((x) => x.ingest(fr));
  for (const ev of fr.events) handleEvent(ev, fr);
  if (S.drive?.unitId && !S.scrub) S.drive.onEvents(fr.events);
  // drop the player into their own soldier when the fight begins
  if (MODE === 'player' && S.drive && !S.drive.unitId && !S.autoPlayed && fr.status === 'RUNNING' && myAvatar()) {
    S.autoPlayed = true;
    playSoldier();
  }
  const t = performance.now();
  if (t - S.lastUi > 250) { S.lastUi = t; updateTop(); renderRoster(); if (S.selected) renderSelection(); updateTimeline(); }
  if (fr.status === 'ENDED' && !S.ended) { S.ended = true; }
}

function myAvatar() { return S.frame?.units.find((u) => u.av && u.pl === S.me) || null; }
function playSoldier() {
  const a = myAvatar();
  if (!a || !S.drive) return toast('Your soldier is not deployed yet', 'warn');
  setView('ground');
  S.drive.start(a.id).then(() => popAlert('ON FOOT — click to aim · WASD move · F take the controls of a vehicle · Enter team chat', 'info')).catch((e) => toast(e.message, 'warn'));
}

// ── team chat ──────────────────────────────────────────────────────────────
function addChat(m) {
  const el = document.createElement('div');
  const cls = m.side === 'RED' ? 'red' : 'blue';
  el.innerHTML = `${m.all ? '<span class="text-muted2">[ALL]</span> ' : ''}<span class="who ${cls}">${esc(m.from)}:</span> ${esc(m.text)}`;
  const log = $('#chat-log');
  log.appendChild(el);
  while (log.children.length > 8) log.firstChild.remove();
  setTimeout(() => el.classList.add('old'), 12000);
  audio?.ui?.('chat');
}
function openChat(all) {
  if (MODE !== 'player') return;
  const box = $('#chat'), inp = $('#chat-in');
  box.classList.add('typing');
  inp.dataset.all = all ? '1' : '';
  inp.placeholder = all ? 'ALL chat — Enter to send, Esc to cancel' : 'Team chat — Enter to send, Esc to cancel';
  S.drive?.keys.clear();
  inp.value = '';
  inp.focus();
}
function closeChat() { $('#chat').classList.remove('typing'); $('#chat-in').blur(); if (S.drive?.unitId) S.ground?.canvas.focus(); }
function wireChat() {
  const inp = $('#chat-in');
  inp.addEventListener('keydown', (e) => {
    e.stopPropagation();
    if (e.key === 'Escape') closeChat();
    else if (e.key === 'Enter') {
      const text = inp.value.trim();
      if (text) sock.request('chat', { text, all: !!inp.dataset.all }).catch((err) => toast(err.message, 'warn'));
      closeChat();
    }
  });
  inp.addEventListener('keyup', (e) => e.stopPropagation());
  window.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' || e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || S.mode?.kind === 'route') return;
    if (!S.frame || S.frame.status === 'DEPLOYMENT') return;
    e.preventDefault();
    openChat(e.shiftKey);
  });
}

function unitName(id) { return S.frame?.units.find((u) => u.id === id)?.n || id; }
function handleEvent(ev, fr) {
  const mine = (side) => MODE === 'admin' || side === S.side || side === 'ALL';
  if (ev.t === 'KILL') {
    const victim = fr.units.find((u) => u.id === ev.victim);
    const vs = victim?.s || (ev.victim.startsWith('B-') ? 'BLUE' : 'RED');
    const as = ev.attacker ? (ev.attacker.startsWith('B-') ? 'BLUE' : 'RED') : null;
    pushFeed(`<span class="mono text-muted2">${fr.tick}</span> <span class="${as === 'BLUE' ? 'side-blue' : 'side-red'}">${esc(ev.attacker || '—')}</span> <i class="bi bi-arrow-right"></i> <span class="${vs === 'BLUE' ? 'side-blue' : 'side-red'}">${esc(ev.victim)}</span> <span class="text-muted2">${esc(ev.round || '')} ${ev.range ? ev.range + ' m' : ''}</span>`);
    if (ev.victim.endsWith('-HVT')) popAlert(`${vs} HVT DESTROYED`, 'crit');
  } else if (ev.t === 'INTRUSION' && mine(ev.side)) {
    const comp = Object.entries(ev.composition).map(([k, n]) => `${n}× ${k}`).join(', ');
    popAlert(`INTRUSION — ${ev.count} hostile(s) in ${ev.side} zone: ${comp}`, 'crit');
    pushFeed(`<span class="text-amber"><i class="bi bi-exclamation-triangle"></i> Intrusion: ${esc(comp)}</span>`);
  } else if (ev.t === 'HVT_ACQUIRED' && mine(ev.side)) {
    popAlert(`${ev.side === S.side || MODE === 'admin' ? `${ev.side} HAS LOCATED THE ENEMY HVT` : ''}`, 'warn');
  } else if (ev.t === 'ALERT' && mine(ev.side)) {
    pushFeed(`<span class="${ev.level === 'crit' ? 'text-red' : ev.level === 'warn' ? 'text-amber' : 'text-muted2'}">${esc(ev.text)}</span>`);
    if (ev.level === 'crit') popAlert(ev.text, 'crit');
  } else if (ev.t === 'DETECT' && mine(ev.side) && MODE !== 'admin') {
    if (S.contacts.has(ev.target)) return; // report each hostile once; flickering detections are not news
    S.contacts.add(ev.target);
    const u = fr.units.find((x) => x.id === ev.target);
    if (u) pushFeed(`<span class="text-muted2"><i class="bi bi-eye"></i> Contact: ${esc(u.c)} at grid ${gridRef(u.x, u.y, S.session.scenario.ao_half_km * 1000)}</span>`);
  } else if (ev.t === 'END') pushFeed(`<b>${esc(ev.reason)}</b>`);
}
function pushFeed(html) { S.feed.unshift(html); S.feed = S.feed.slice(0, 120); renderFeed(); }
function renderFeed() { $('#feed').innerHTML = S.feed.map((h) => `<div class="feed-item">${h}</div>`).join(''); }
function popAlert(text, level = 'info') {
  if (!text) return;
  const el = document.createElement('div');
  el.className = `alert-pop ${level}`;
  el.textContent = text;
  $('#alerts').appendChild(el);
  setTimeout(() => el.remove(), 5000);
}
function setBanner(text) { const b = $('#mode-banner'); b.style.display = text ? '' : 'none'; b.textContent = text || ''; }

function fmtTime(s) { s = Math.max(0, Math.round(s)); return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`; }
function updateTop() {
  const lb = S.lobby, fr = S.frame;
  if (lb) {
    $('#s-phase').textContent = (fr?.status || lb.status).replace('DEPLOYMENT', 'DEPLOY');
    const tickS = lb.tick_s || S.session?.scenario.tick_s || 0.25;
    const tick = fr?.tick ?? lb.tick;
    $('#s-time').textContent = fmtTime((lb.max_ticks - tick) * tickS);
    $('#s-tick').textContent = tick;
  }
  if (!fr) return;
  $('#s-kb').textContent = fr.kills.BLUE;
  $('#s-kr').textContent = fr.kills.RED;
  const own = MODE === 'admin' ? 'BLUE' : S.side, enemy = own === 'BLUE' ? 'RED' : 'BLUE';
  const h = (side) => fr.units.find((u) => u.hvt && u.s === side);
  const ho = h(own), he = h(enemy);
  $('#s-hvt-own').textContent = ho ? (ho.st === 'DESTROYED' ? 'DESTROYED' : `${Math.round((ho.hp / ho.mhp) * 100)}%`) : '—';
  $('#s-hvt-enemy').textContent = he ? (he.st === 'DESTROYED' ? 'DESTROYED' : `${Math.round((he.hp / he.mhp) * 100)}%`) : 'UNKNOWN';
  $('#s-hvt-enemy').className = `v ${he ? 'text-amber' : 'text-muted2'}`;
  if (MODE === 'admin') { $('.statbox:nth-child(6) .k').textContent = 'Blue HVT'; $('.statbox:nth-child(7) .k').textContent = 'Red HVT'; }
}

// ── roster & selection (l. Situational displays) ──────────────────────────
function renderRoster(force) {
  const fr = S.scrub ? S.scrubFrame || S.frame : S.frame;
  if (!fr) return;
  const sides = MODE === 'admin' ? ['BLUE', 'RED'] : [S.side];
  let html = '';
  for (const side of sides) {
    for (const fid of [...FORMS, null]) {
      const units = fr.units.filter((u) => u.s === side && (fid ? u.f === fid : !u.f));
      if (!units.length) continue;
      const fo = fr.formations.find((f) => f.s === side && f.id === fid);
      const slot = S.lobby?.slots.find((s) => s.side === side && s.slot === fid);
      const ctl = fo ? (fo.ctl === 'AI' ? '<i class="bi bi-cpu"></i> AI' : esc(slot?.callsign || 'player')) : '';
      html += `<div class="form-head"><span class="${side === 'RED' ? 'side-red' : 'side-blue'}">${fid ? `${side === S.side || MODE === 'admin' ? side + ' ' : ''}${fid}` : 'HVT'} ${fo ? `<span class="text-muted2">· ${fo.d}</span>` : ''}</span><span>${ctl}</span></div>`;
      for (const u of units) {
        const ammoTot = Object.values(u.am || {}).reduce((a, b) => a + b, 0);
        const ammoMax = Object.keys(u.am || {}).reduce((a, w) => a + (S.session.catalogue.weapons[w]?.ammo || 0), 0) || 1;
        const hp = (u.hp / u.mhp) * 100;
        html += `<div class="unit-row ${u.id === S.selected ? 'sel' : ''} ${u.st === 'DESTROYED' ? 'dead' : ''}" data-u="${u.id}">
          <i class="bi ${CLS_ICON[u.c] || 'bi-square'} ${side === 'RED' ? 'side-red' : 'side-blue'}"></i>
          <span>${esc(u.n)} ${u.mk ? '<i class="bi bi-lock-fill text-red" title="Immobilised"></i>' : ''}${u.ctl && u.ctl !== 'AI' ? ' <i class="bi bi-joystick text-amber"></i>' : ''} <span class="text-muted2">${u.tk || ''}</span></span>
          <span class="bars"><span class="bar hp"><i style="width:${hp}%;background-position:${100 - hp}% 0"></i></span>${u.am ? `<span class="bar am"><i style="width:${(ammoTot / ammoMax) * 100}%"></i></span><span class="bar fu"><i style="width:${(u.fu / u.fm) * 100}%"></i></span>` : ''}</span></div>`;
      }
    }
  }
  if (html !== S.rosterHtml || force) {
    S.rosterHtml = html;
    $('#roster').innerHTML = html;
    $('#roster').querySelectorAll('[data-u]').forEach((r) => (r.onclick = () => { allScenes().forEach((x) => x.select(r.dataset.u)); flyToUnit(r.dataset.u); }));
  }
}
function flyToUnit(id) {
  const u = S.frame?.units.find((x) => x.id === id);
  if (!u) return;
  if (S.viewMode === 'ground') { S.ground.flyTo(u.x, u.y - 60, 120); return; }
  const [lon, lat] = S.g.frame.toGeo(u.x, u.y - 900);
  S.g.viewer.camera.flyTo({ destination: C.Cartesian3.fromDegrees(lon, lat, u.z + 700), orientation: { heading: 0, pitch: -38 * Math.PI / 180, roll: 0 }, duration: 0.9 });
}

function renderSelection() {
  const id = S.selected;
  const panel = $('#sel-panel');
  const u = id && S.frame?.units.find((x) => x.id === id);
  if (!u) { panel.style.display = 'none'; return; }
  panel.style.display = '';
  const spec = S.session.catalogue.platforms[u.p] || {};
  const own = MODE === 'player' && u.s === S.side;
  const fo = S.frame.formations.find((f) => f.s === u.s && f.id === u.f);
  const canCmd = own && (amCommander() || mySlot() === u.f);
  $('#sel-title').innerHTML = `<span class="${u.s === 'RED' ? 'side-red' : 'side-blue'}">${esc(u.n)}</span>`;
  const hp = Math.round((u.hp / u.mhp) * 100);
  const weapons = u.am ? Object.entries(u.am).map(([w, n]) => `${esc(S.session.catalogue.weapons[w]?.name || w)}: <b>${n}</b>${u.rl?.[w] > 0 ? ` <span class="text-muted2">(${u.rl[w].toFixed(1)}s)</span>` : ''}`).join('<br>') : '<span class="text-muted2">—</span>';
  let html = `<div class="mb-1"><b>${esc(spec.name || u.p)}</b> <span class="text-muted2">${u.c}</span></div>
    <div class="row g-1 mb-2"><div class="col-6">HP <b>${hp}%</b> · Morale <b>${Math.round(u.m)}</b></div><div class="col-6">${u.fu != null ? `Fuel <b>${Math.round((u.fu / u.fm) * 100)}%</b>` : ''}</div>
    <div class="col-6">Speed <b>${Math.abs(u.v * 3.6).toFixed(0)}</b> km/h</div><div class="col-6">Hdg <b>${Math.round(u.h)}° ${compass(u.h)}</b></div>
    <div class="col-6">Status <b>${u.st}</b>${u.mk ? ' <span class="text-red">IMMOB</span>' : ''}</div><div class="col-6">Ground <b>${u.gr}</b>${u.sw ? ' ≈' : ''}</div>
    <div class="col-6">Grid <b class="mono">${gridRef(u.x, u.y, S.session.scenario.ao_half_km * 1000)}</b></div><div class="col-6">${u.tk ? `Task <b>${u.tk}</b>` : ''}</div></div>
    ${u.am ? `<div class="mb-2">${weapons}</div>` : ''}`;
  html += `<div class="d-flex flex-wrap gap-1">`;
  html += `<button class="btn btn-sm btn-tac" data-act="follow"><i class="bi bi-camera-reels"></i> Spectate</button>`;
  if (canCmd && u.st !== 'DESTROYED' && !u.hvt) {
    html += `<button class="btn btn-sm btn-warning hud-font" data-act="drive"><i class="bi bi-joystick"></i> Take control</button>
      <button class="btn btn-sm btn-tac" data-act="route-unit"><i class="bi bi-signpost-split"></i> Unit route</button>
      <button class="btn btn-sm btn-tac" data-act="halt-unit"><i class="bi bi-stop-circle"></i> Halt</button>
      <button class="btn btn-sm btn-tac" data-act="rejoin"><i class="bi bi-arrow-return-left"></i> Rejoin</button>`;
  }
  html += `</div>`;
  if (canCmd && fo) {
    const indirect = fo.u.some((uid) => { const x = S.frame.units.find((y) => y.id === uid); return x && Object.keys(x.am || {}).some((w) => S.session.catalogue.weapons[w]?.indirect); });
    html += `<hr class="my-2"><div class="hud-font text-muted2 mb-1">FORMATION ${fo.id} · ${fo.ctl === 'AI' ? 'AI commanded' : 'player commanded'}</div>
      <div class="row g-1 mb-1"><div class="col-6"><select class="form-select form-select-sm" id="f-doc">${DOCTRINES.map((d) => `<option ${d === fo.d ? 'selected' : ''}>${d}</option>`).join('')}</select></div>
      <div class="col-6"><select class="form-select form-select-sm" id="f-shape">${SHAPES.map((d) => `<option ${d === fo.sh ? 'selected' : ''}>${d}</option>`).join('')}</select></div></div>
      <div class="d-flex flex-wrap gap-1"><button class="btn btn-sm btn-tac" data-act="route-form"><i class="bi bi-signpost-2"></i> Plot route</button>
      <button class="btn btn-sm btn-tac" data-act="halt-form"><i class="bi bi-stop-fill"></i> Halt</button>
      ${indirect ? '<button class="btn btn-sm btn-tac-red" data-act="fire"><i class="bi bi-bullseye"></i> Fire mission</button>' : ''}</div>`;
  }
  $('#sel-body').innerHTML = html;
  const b = $('#sel-body');
  const cmd = (c) => sock.request('cmd', { cmd: c }).then(() => toast('Order sent', 'ok', 1500)).catch((e) => toast(e.message, 'warn'));
  b.querySelectorAll('[data-act]').forEach((btn) => (btn.onclick = () => {
    const a = btn.dataset.act;
    if (a === 'follow') { if (S.ground) { setView('ground'); S.ground.follow(id); popAlert('SPECTATE · press C to change camera, Esc to exit', 'info'); } else S.scene.follow(id); }
    if (a === 'drive') { if (!S.drive) return toast('3D view unavailable', 'warn'); setView('ground'); S.drive.start(id).catch((e) => toast(e.message, 'warn')); }
    if (a === 'route-unit') startRoute({ unit_ids: [id] });
    if (a === 'route-form') startRoute({ formation: fo.id });
    if (a === 'halt-unit') cmd({ type: 'halt', unit_ids: [id] });
    if (a === 'halt-form') cmd({ type: 'halt', formation: fo.id });
    if (a === 'rejoin') cmd({ type: 'rejoin', unit_ids: [id] });
    if (a === 'fire') { S.mode = { kind: 'fire', formation: fo.id }; setBanner(`Click the target area for ${fo.id}'s fire mission · Esc to cancel`); }
  }));
  const doc = b.querySelector('#f-doc'), sh = b.querySelector('#f-shape');
  if (doc) doc.onchange = () => cmd({ type: 'doctrine', formation: fo.id, doctrine: doc.value });
  if (sh) sh.onchange = () => cmd({ type: 'shape', formation: fo.id, shape: sh.value });
}

// ── views: 3D battle (Three.js) and map (Cesium) ───────────────────────────
function allScenes() { return [S.scene, S.ground].filter(Boolean); }
function cur() { return S.viewMode === 'ground' && S.ground ? S.ground : S.scene; }
function setView(mode) {
  if (mode === 'ground' && !S.ground) mode = 'map';
  S.viewMode = mode;
  $('#ground').style.display = mode === 'ground' ? '' : 'none';
  $('#globe').style.visibility = mode === 'map' ? 'visible' : 'hidden';
  if (S.g) S.g.viewer.useDefaultRenderLoop = mode === 'map';
  if (S.ground) S.ground.setEnabled(mode === 'ground');
  const b = $('#t-mode');
  if (b) b.innerHTML = mode === 'ground' ? '<i class="bi bi-map"></i> MAP (M)' : '<i class="bi bi-badge-3d"></i> 3D VIEW (M)';
}

/** Clicks in the 3D view: select units, or place waypoints / fire missions / deployment points. */
function wireGround() {
  const cv = S.ground.canvas;
  let down = null;
  cv.addEventListener('pointerdown', (e) => { down = [e.clientX, e.clientY]; });
  cv.addEventListener('pointerup', (e) => {
    if (!down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 6 || S.drive?.unitId || e.button !== 0) return;
    const r = cv.getBoundingClientRect();
    const sp = { x: e.clientX - r.left, y: e.clientY - r.top };
    onMapClick(sp, () => S.ground.pickLocal(sp), () => S.ground.pickUnit(sp));
  });
  cv.addEventListener('dblclick', () => { if (S.mode?.kind === 'route') finishRoute(true); });
  cv.addEventListener('contextmenu', (e) => { if (S.mode?.kind === 'route') { e.preventDefault(); finishRoute(true); } });
  cv.addEventListener('wheel', (e) => { if (S.ground.camMode === 'follow') S.ground.rangeMul = Math.max(0.35, Math.min(40, S.ground.rangeMul * (e.deltaY > 0 ? 1.15 : 0.87))); }, { passive: true });
}

function onMapClick(screen, pickPoint, pickUnit) {
  if (S.placing && S.placing !== '__view') { finishPlacing(pickPoint()); return; }
  if (S.mode?.kind === 'route') { const pt = pickPoint(); if (pt) { S.routeDraft.push([pt[0], pt[1]]); drawDraft(); setBanner(`Route: ${S.routeDraft.length} waypoint(s) · double-click / right-click / Enter to send · Esc to cancel`); } return; }
  if (S.mode?.kind === 'fire') {
    const fid = S.mode.formation, pt = pickPoint();
    S.mode = null; setBanner(null);
    if (pt) sock.request('cmd', { cmd: { type: 'fireMission', formation: fid, target: [Math.round(pt[0]), Math.round(pt[1])], rounds: 8 } }).then(() => toast(`Fire mission: ${fid} → grid ${gridRef(pt[0], pt[1], S.session.scenario.ao_half_km * 1000)}`, 'ok')).catch((er) => toast(er.message, 'warn'));
    return;
  }
  const id = pickUnit();
  allScenes().forEach((x) => x.select(id));
}

// ── route plotting & map clicks (BP-3, LS-5) ──────────────────────────────
let draftEnt = null;
function startRoute(target) {
  S.mode = { kind: 'route', target };
  S.routeDraft = [];
  setBanner('Click waypoints on the map · double-click or Enter to send · Esc to cancel');
}
function drawDraft() {
  if (draftEnt) S.g.viewer.entities.remove(draftEnt);
  const u = S.frame?.units.find((x) => x.id === (S.mode.target.unit_ids?.[0] || S.frame.formations.find((f) => f.s === S.side && f.id === S.mode.target.formation)?.u[0]));
  const pts = [...(u ? [[u.x, u.y]] : []), ...S.routeDraft];
  if (pts.length < 2) return;
  draftEnt = S.g.viewer.entities.add({ polyline: { positions: pts.map(([x, y]) => S.g.frame.cart(x, y)), width: 4, clampToGround: true, material: new C.PolylineDashMaterialProperty({ color: C.Color.YELLOW, dashLength: 14 }) } });
}
async function finishRoute(send) {
  const m = S.mode;
  S.mode = null;
  setBanner(null);
  if (draftEnt) { S.g.viewer.entities.remove(draftEnt); draftEnt = null; }
  if (!send || !S.routeDraft.length) return;
  try { await sock.request('cmd', { cmd: { type: 'route', ...m.target, waypoints: S.routeDraft.map(([x, y]) => [Math.round(x), Math.round(y)]) } }); toast('Route sent', 'ok', 1500); }
  catch (e) { toast(e.message, 'warn'); }
}

function wireMap() {
  const h = new C.ScreenSpaceEventHandler(S.g.scene.canvas);
  h.setInputAction((e) => {
    if (S.drive?.unitId) return;
    onMapClick(e.position, () => pickLocal(S.g, e.position), () => S.scene.pickUnit(e.position));
  }, C.ScreenSpaceEventType.LEFT_CLICK);
  h.setInputAction(() => { if (S.mode?.kind === 'route') finishRoute(true); }, C.ScreenSpaceEventType.LEFT_DOUBLE_CLICK);
  h.setInputAction(() => { if (S.mode?.kind === 'route') finishRoute(true); }, C.ScreenSpaceEventType.RIGHT_CLICK);
  window.addEventListener('keydown', (e) => {
    if (e.target.tagName === 'INPUT') return;
    if (e.key === 'Escape') { if (S.mode?.kind === 'route') finishRoute(false); else if (S.mode) { S.mode = null; setBanner(null); } if (S.placing) finishPlacing(null); if (cur().camMode === 'follow') cur().freeCam(); }
    if (e.key.toLowerCase() === 'm' && !S.drive?.unitId) setView(S.viewMode === 'map' ? 'ground' : 'map');
    if (e.key.toLowerCase() === 'p' && !S.drive?.unitId && MODE === 'player' && S.frame?.status === 'RUNNING') playSoldier();
    if (e.key === 'Enter' && S.mode?.kind === 'route') finishRoute(true);
  });
  S.g.scene.canvas.addEventListener('wheel', (e) => { if (cur().camMode === 'follow') cur().rangeMul = Math.max(0.35, Math.min(40, S.scene.rangeMul * (e.deltaY > 0 ? 1.15 : 0.87))); }, { passive: true });
  window.addEventListener('keydown', (e) => {
    if (e.target.tagName === 'INPUT' || S.drive?.unitId) return;
    if (e.key.toLowerCase() === 'c' && cur().camMode === 'follow') { const v = cur().cycleView(e.shiftKey ? -1 : 1); popAlert(`CAMERA · ${v.label}`, 'info'); }
  });
}

// ── advisor (AI-1, AI-2) ───────────────────────────────────────────────────
function renderAdvisor(a) {
  if (!a) return;
  $('#adv-tempo').innerHTML = `<span class="tempo-${a.tempo} hud-font fw-bold">${a.tempo}</span>`;
  const coas = a.coas.map((c) => `<div class="coa"><div><span class="rank">#${c.rank}</span> <b>${esc(c.title)}</b></div><div class="text-muted2 my-1">${esc(c.rationale)}</div>
    ${c.order && MODE === 'player' ? `<button class="btn btn-sm btn-tac" data-coa="${c.id}"><i class="bi bi-check2"></i> Execute (confirm)</button>` : ''}</div>`).join('');
  $('#advisor').innerHTML = `<div class="mb-1 text-muted2 mono">tick ${a.tick} · ${a.side} · ${a.source === 'llm' ? 'LLM' : 'analyst'}</div>
    <div class="fw-bold mb-1">${esc(a.headline)}</div><div class="mb-1"><i class="bi bi-exclamation-diamond text-amber"></i> ${esc(a.risk)}</div>
    <div class="text-muted2 mb-2">${esc(a.detail)}</div>${coas}`;
  $('#advisor').querySelectorAll('[data-coa]').forEach((b) => (b.onclick = async () => {
    const coa = a.coas.find((c) => c.id === b.dataset.coa);
    if (!confirm(`Execute recommendation?\n\n${coa.title}\n\n${coa.rationale}`)) return;
    try { await sock.request('advisor_apply', { coa_id: b.dataset.coa }); toast('Recommendation executed', 'ok'); b.disabled = true; } catch (e) { toast(e.message, 'warn'); }
  }));
}

// ── timeline: rewind while live (LS-10) ───────────────────────────────────
function updateTimeline() {
  const tl = $('#tl');
  tl.max = S.liveTick;
  if (!S.scrub) tl.value = S.liveTick;
  const tickS = S.session?.scenario.tick_s || 0.25;
  $('#tl-left').textContent = `t+${fmtTime((+tl.value) * tickS)}`;
  $('#tl-right').textContent = S.liveTick;
}
let scrubReq = 0;
async function scrubTo(t) {
  S.scrub = true;
  $('#tl-live').style.display = '';
  setBanner(`REWIND — viewing tick ${t} (battle continues live)`);
  const id = ++scrubReq;
  let fr = S.history.get(t);
  if (!fr) {
    try { const r = await sock.request('history', { from: Math.max(0, t - 2), to: t + 2 }); for (const f of r.frames) S.history.set(f.tick, f); fr = S.history.get(t) || r.frames[r.frames.length - 1]; } catch (e) { toast(e.message, 'warn'); }
  }
  if (fr && id === scrubReq) { S.scrubFrame = fr; allScenes().forEach((x) => x.ingest(fr, { instant: true })); renderRoster(); }
  $('#tl-left').textContent = `t+${fmtTime(t * (S.session?.scenario.tick_s || 0.25))}`;
}
function goLive() {
  S.scrub = false; S.scrubFrame = null;
  $('#tl-live').style.display = 'none';
  setBanner(null);
  if (S.frame) allScenes().forEach((x) => x.ingest(S.frame, { instant: true }));
}

// ── controls ───────────────────────────────────────────────────────────────
function updatePace() {
  const lb = S.lobby;
  const can = amCommander();
  $('#pace').style.display = can ? '' : 'none';
  if (!lb) return;
  $('#c-start').style.display = lb.status === 'DEPLOYMENT' ? '' : 'none';
  $('#c-pause').innerHTML = lb.status === 'PAUSED' ? '<i class="bi bi-play-fill"></i>' : '<i class="bi bi-pause-fill"></i>';
  $('#c-auto').classList.toggle('active', lb.auto);
}
function ctl(action, value) { return sock.request('control', { action, value }).catch((e) => toast(e.message, 'warn')); }

function wireUi() {
  $('#c-start').onclick = () => (MODE === 'admin' ? ctl('start') : sock.request('ready', { ready: true }).catch((e) => toast(e.message, 'warn')));
  $('#c-pause').onclick = () => ctl(S.lobby?.status === 'PAUSED' ? 'resume' : 'pause');
  $('#c-step').onclick = () => ctl('step');
  $('#c-auto').onclick = () => ctl('auto', !S.lobby?.auto);
  $('#c-speed').onchange = (e) => ctl('cadence', Math.round((S.session.scenario.tick_s * 1000) / +e.target.value));
  $('#tl').oninput = (e) => scrubTo(+e.target.value);
  $('#tl-live').onclick = goLive;
  $('#t-home').onclick = () => { cur().freeCam(); if (S.viewMode === 'ground') S.ground.home(); else home(S.g); };
  $('#t-2d').onclick = () => toggle2D(S.g);
  $('#t-free').onclick = () => cur().freeCam();
  $('#t-mode').onclick = () => setView(S.viewMode === 'map' ? 'ground' : 'map');
  $('#t-view').onclick = () => {
    if (S.ground && S.viewMode !== 'ground') setView('ground');
    if (cur().camMode === 'free') { if (S.selected) cur().follow(S.selected); else return toast('Select a unit to spectate first', 'info'); }
    const v = cur().cycleView(1);
    popAlert(`CAMERA · ${v.label}`, 'info');
  };
  $('#search-go').onclick = () => search(S.g, $('#search').value).catch((e) => toast(e.message, 'warn'));
  $('#search').onkeydown = (e) => { if (e.key === 'Enter') $('#search-go').click(); };
  $('#l-zones').onchange = (e) => setLayer(S.g, 'zones', e.target.checked);
  $('#l-corridors').onchange = (e) => setLayer(S.g, 'corridors', e.target.checked);
  $('#l-grid').onchange = (e) => setLayer(S.g, 'grid', e.target.checked);
  $('#l-borders').onchange = (e) => setLayer(S.g, 'borders', e.target.checked);
  $('#l-labels').onchange = (e) => allScenes().forEach((x) => (x.showLabels = e.target.checked));
  $('#l-fog').onchange = (e) => sock.request('fog', { enabled: e.target.checked }).then(() => toast(`Fog of war ${e.target.checked ? 'enabled' : 'disabled'} for players`, 'ok'));
  $('#l-imagery').onchange = (e) => setImagery(S.g, e.target.value);
  $('#l-quality').value = localStorage.getItem('wg_quality') || 'high';
  $('#l-sens').value = localStorage.getItem('wg_sens') || 1;
  $('#l-sens').oninput = (e) => { localStorage.setItem('wg_sens', e.target.value); if (S.drive) S.drive.sens = +e.target.value; };
  $('#l-vol').value = localStorage.getItem('wg_volume') ?? 0.8;
  $('#l-vol').oninput = (e) => audio.setVolume(+e.target.value);
  document.addEventListener('pointerdown', () => audio.unlock(), { once: false });
  $('#l-quality').onchange = (e) => { localStorage.setItem('wg_quality', e.target.value); toast('Graphics quality saved — reloading the 3D view', 'info', 1500); setTimeout(() => location.reload(), 800); };
  $('#l-sensor').onchange = (e) => allScenes().forEach((x) => x.setSensor(e.target.value));
  $('#sel-close').onclick = () => allScenes().forEach((x) => x.select(null));
  $('#adv-req').onclick = async () => {
    try { const r = await sock.request('advisor_request', MODE === 'admin' ? { side: 'BLUE' } : {}); renderAdvisor(r.assessment); } catch (e) { toast(e.message, 'warn'); }
  };
  $('#lb-ready').onclick = () => {
    const side = planSide();
    sock.request('ready', { side, ready: !S.lobby.ready[side] }).catch((e) => toast(e.message, 'warn'));
  };
  $('#lb-force').onclick = () => ctl('start');
  $('#lb-hvt').onclick = () => startPlacing('HVT');
  $('#lb-map').onclick = () => { $('#lobby').style.display = 'none'; setBanner('Map view — press Esc or click here to return to deployment'); S.placing = '__view'; };
  $('#mode-banner').onclick = () => { if (S.placing === '__view') { S.placing = null; setBanner(null); $('#lobby').style.display = ''; } };
  $('#lb-suggest').onclick = async () => {
    try {
      const r = await sock.request('suggest', MODE === 'admin' ? { side: planSide() } : {});
      const s = r.suggestion;
      $('#sug-why').textContent = s.rationale;
      $('#sug-src').textContent = s.source === 'llm' ? 'LLM' : 'ANALYST';
      const P = S.session.catalogue.platforms;
      $('#sug-table').innerHTML = `<table class="table table-sm"><thead><tr><th>Formation</th><th>Doctrine</th><th>Shape</th><th>Composition</th></tr></thead><tbody>${s.formations.map((f) => `<tr><td>${f.id}</td><td>${f.doctrine}</td><td>${f.shape}</td><td>${Object.entries(f.composition).map(([p, n]) => `${n}× ${esc(P[p]?.name || p)}`).join(', ')}</td></tr>`).join('')}</tbody></table>`;
      const m = new bootstrap.Modal($('#sug-modal'));
      m.show();
      $('#sug-accept').onclick = () => {
        m.hide();
        const mine = MODE === 'admin' || amCommander() ? s.formations : s.formations.filter((f) => f.id === mySlot());
        for (const f of mine) { const i = S.plan.formations.findIndex((x) => x.id === f.id); if (i >= 0) S.plan.formations[i] = f; else S.plan.formations.push(f); }
        if ((MODE === 'admin' || amCommander()) && s.hvt) S.plan.hvt = s.hvt;
        pushPlan();
        toast('Suggestion loaded — adjust as you see fit', 'ok');
      };
    } catch (e) { toast(e.message, 'warn'); }
  };
  if (MODE === 'admin') {
    const sel = document.createElement('select');
    sel.className = 'form-select form-select-sm';
    sel.style.width = '120px';
    sel.innerHTML = '<option>BLUE</option><option>RED</option>';
    sel.onchange = () => { S.adminPlanSide = sel.value; S.plan = structuredClone(S.lobby.plans[sel.value] || { side: sel.value, formations: [], hvt: null }); renderLobby(); renderDeployMarks(); };
    $('#lb-suggest').before(sel);
  }
  $('#r-again').onclick = async () => { try { await sock.request('play_again'); } catch (e) { toast(e.message, 'warn'); } };
}

// ── end of battle (BP-6) ───────────────────────────────────────────────────
function onEnded(res, battleId) {
  S.ended = true;
  const me = MODE === 'admin' ? null : S.side;
  let text, cls;
  if (res.outcome === 'STALEMATE') { text = 'STALEMATE'; cls = 'draw'; }
  else if (res.outcome === 'DRAW') { text = 'DRAW'; cls = 'draw'; }
  else if (!me) { text = `${res.outcome} VICTORY`; cls = res.outcome === 'BLUE' ? 'win' : 'loss'; }
  else { text = res.outcome === me ? 'VICTORY' : 'DEFEAT'; cls = res.outcome === me ? 'win' : 'loss'; }
  $('#r-banner').textContent = text;
  $('#r-banner').className = `result-banner ${cls}`;
  $('#r-reason').textContent = res.reason;
  const k = (label, v, c = '') => `<div class="col-6 col-md-3"><div class="kpi"><div class="v ${c}">${v}</div><div class="k">${label}</div></div></div>`;
  $('#r-kpis').innerHTML = k('Blue kills', res.kills.BLUE, 'side-blue') + k('Red kills', res.kills.RED, 'side-red') + k('Blue surviving', `${res.surviving.BLUE}/${res.deployed.BLUE}`, 'side-blue') + k('Red surviving', `${res.surviving.RED}/${res.deployed.RED}`, 'side-red')
    + (res.mode === 'SKIRMISH' ? '' : k('Blue HVT', `${res.hvt_hp.BLUE}%`) + k('Red HVT', `${res.hvt_hp.RED}%`)) + k('Ticks', res.ticks) + k('Match time', fmtTime(res.sim_time));
  $('#r-aar').href = `/aar/${battleId}/${S.token ? `?token=${encodeURIComponent(S.token)}` : ''}`;
  $('#r-again').style.display = amCommander() ? '' : 'none';
  if (S.drive?.unitId) S.drive.stop(false);
  new bootstrap.Modal($('#result-modal')).show();
}
