// After Action Review (n, AR-1..3, AI-3): full 3D replay from persisted ticks, results dashboard,
// kill feed and "Ask the Battle".
import { api, esc, toast } from './api.js';
import { createGlobe, drawCorridors, drawZones, flyLocal, home } from './globe.js';
import { gridRef } from './geo.js';
import { BattleScene } from './scene.js';

const $ = (s) => document.querySelector(s);
const BID = $('#app').dataset.bid;
window.__AAR = { get scene() { return scene; }, get g() { return g; } };
const params = new URLSearchParams(location.search);
const token = params.get('token') || undefined;
const CHUNK = 200;
const BLUE = '#36b8ff', RED = '#ff4b3e';
Chart.defaults.color = '#8399ab';
Chart.defaults.borderColor = 'rgba(120,170,210,.15)';
Chart.defaults.font.family = 'Inter, sans-serif';

let B, g, scene;
const R = { tick: 0, last: 0, playing: false, speed: 4, cache: new Map(), pending: new Map(), shown: -1 };

async function chunk(start) {
  if (R.pending.has(start)) return R.pending.get(start);
  const p = api(`battles/${BID}/ticks/?from=${start}&to=${start + CHUNK - 1}`, { token }).then((r) => {
    for (const f of r.frames) R.cache.set(f.tick, f);
    if (R.cache.size > 2500) { const keys = [...R.cache.keys()].sort((a, b) => a - b); for (const k of keys) { if (Math.abs(k - R.tick) > 1200) R.cache.delete(k); if (R.cache.size < 1800) break; } }
  }).catch((e) => toast(e.message, 'warn')).finally(() => R.pending.delete(start));
  R.pending.set(start, p);
  return p;
}
function prefetch() {
  const c = Math.floor(R.tick / CHUNK) * CHUNK;
  for (const s of [c, c + CHUNK]) if (s <= R.last && !R.cache.has(Math.max(1, s)) && !R.pending.has(s)) chunk(s);
}
async function show(t, jump) {
  t = Math.max(1, Math.min(R.last, Math.round(t)));
  if (!R.cache.has(t)) { await chunk(Math.floor(t / CHUNK) * CHUNK); }
  const f = R.cache.get(t);
  if (!f) return;
  scene.ingest(f, { instant: jump, effects: !jump && R.speed <= 8 });
  R.shown = t;
  $('#scrub').value = t;
  $('#t-now').textContent = `${t} · t+${fmt(f.sim_time)}`;
  if (scene.selected) renderSel(scene.selected);
}
const fmt = (s) => `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(Math.round(s % 60)).padStart(2, '0')}`;

function loop() {
  let lastT = performance.now();
  const step = () => {
    const now = performance.now();
    const dt = (now - lastT) / 1000;
    lastT = now;
    if (R.playing) {
      R.tick += (dt / B.scenario.tick_s) * R.speed;
      if (R.tick >= R.last) { R.tick = R.last; R.playing = false; $('#play').innerHTML = '<i class="bi bi-play-fill"></i>'; }
      const t = Math.floor(R.tick);
      if (t !== R.shown && R.cache.has(t)) {
        // at high speed skip intermediate frames but keep the most recent
        show(t, t - R.shown > 4);
      }
      prefetch();
    }
    requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

function renderSel(id) {
  const u = scene.view(id);
  if (!u) { $('#sel').textContent = '—'; return; }
  const p = B.catalogue.platforms[u.p] || {};
  $('#sel').innerHTML = `<b class="${u.s === 'RED' ? 'side-red' : 'side-blue'}">${esc(u.n)}</b> · ${esc(p.name || u.p)} · ${u.c} · HP ${Math.round((u.hp / u.mhp) * 100)}% · morale ${Math.round(u.m)} · ${u.st}${u.mk ? ' · IMMOBILISED' : ''} · ${Math.abs(u.v * 3.6).toFixed(0)} km/h · grid ${gridRef(u.x, u.y, B.scenario.ao_half_km * 1000)} · task ${u.tk || '—'} · kills ${u.k}`;
}

// ── dashboard (AR-3) ───────────────────────────────────────────────────────
async function dashboard() {
  const a = await api(`battles/${BID}/analytics/`, { token });
  const out = a.outcome;
  $('#banner').textContent = out === 'STALEMATE' || out === 'DRAW' ? out : out ? `${out} VICTORY` : 'IN PROGRESS';
  $('#banner').className = `result-banner ${out === 'BLUE' ? 'win' : out === 'RED' ? 'loss' : 'draw'}`;
  $('#reason').textContent = a.reason || '';
  const k = (label, v, c = '') => `<div class="col-4"><div class="kpi"><div class="v ${c}">${v}</div><div class="k">${label}</div></div></div>`;
  $('#kpis').innerHTML = k('Blue deployed', a.deployed.BLUE, 'side-blue') + k('Blue attrited', a.lost.BLUE, 'side-blue') + k('Blue surviving', a.surviving.BLUE, 'side-blue')
    + k('Red deployed', a.deployed.RED, 'side-red') + k('Red attrited', a.lost.RED, 'side-red') + k('Red surviving', a.surviving.RED, 'side-red')
    + k('Blue kills', a.kills.BLUE, 'side-blue') + k('Red kills', a.kills.RED, 'side-red') + k('Kill ratio B:R', `${a.kill_ratio.BLUE}`, '');
  const s = a.series;
  const labels = s.map((x) => fmt(x.sim_time));
  const line = (el, sets, opts = {}) => new Chart($(el), { type: 'line', data: { labels, datasets: sets }, options: { animation: false, maintainAspectRatio: false, interaction: { mode: 'index', intersect: false }, elements: { point: { radius: 0 } }, plugins: { legend: { labels: { boxWidth: 10 } } }, scales: { x: { ticks: { maxTicksLimit: 8 } } }, ...opts } });
  line('#c-strength', [
    { label: 'BLUE strength (pts)', yAxisID: 'y', data: s.map((x) => x.blue_strength), borderColor: BLUE, backgroundColor: 'rgba(54,184,255,.12)', fill: true, tension: 0.2 },
    { label: 'RED strength (pts)', yAxisID: 'y', data: s.map((x) => x.red_strength), borderColor: RED, backgroundColor: 'rgba(255,75,62,.12)', fill: true, tension: 0.2 },
    { label: 'BLUE alive', data: s.map((x) => x.blue_alive), borderColor: BLUE, borderDash: [4, 4], yAxisID: 'y2' },
    { label: 'RED alive', data: s.map((x) => x.red_alive), borderColor: RED, borderDash: [4, 4], yAxisID: 'y2' },
  ], { scales: { x: { ticks: { maxTicksLimit: 8 } }, y: { position: 'left', beginAtZero: true, title: { display: true, text: 'combat power (pts)' } }, y2: { position: 'right', beginAtZero: true, title: { display: true, text: 'units alive' }, grid: { drawOnChartArea: false } } } });
  line('#c-hvt', [
    { label: 'BLUE HVT %', data: s.map((x) => x.blue_hvt_hp), borderColor: BLUE, tension: 0.1 },
    { label: 'RED HVT %', data: s.map((x) => x.red_hvt_hp), borderColor: RED, tension: 0.1 },
  ], { scales: { x: { ticks: { maxTicksLimit: 8 } }, y: { min: 0, max: 100 } } });
  const cmp = a.comparative;
  const maxFp = Math.max(cmp.BLUE.firepower, cmp.RED.firepower, 1), maxL = Math.max(cmp.BLUE.lethality, cmp.RED.lethality, 1), maxKr = Math.max(cmp.BLUE.kill_ratio, cmp.RED.kill_ratio, 1);
  const radar = (sd) => [cmp[sd].firepower / maxFp * 100, cmp[sd].survivability, cmp[sd].lethality / maxL * 100, cmp[sd].hvt_integrity, cmp[sd].kill_ratio / maxKr * 100];
  new Chart($('#c-radar'), { type: 'radar', data: { labels: ['Firepower', 'Survivability', 'Lethality', 'HVT integrity', 'Kill ratio'],
    datasets: [{ label: 'BLUE', data: radar('BLUE'), borderColor: BLUE, backgroundColor: 'rgba(54,184,255,.2)' }, { label: 'RED', data: radar('RED'), borderColor: RED, backgroundColor: 'rgba(255,75,62,.2)' }] },
    options: { animation: false, maintainAspectRatio: false, scales: { r: { min: 0, max: 100, ticks: { display: false }, grid: { color: 'rgba(120,170,210,.15)' }, angleLines: { color: 'rgba(120,170,210,.15)' } } } } });
  const wk = Object.entries(a.by_weapon).sort((x, y) => y[1] - x[1]);
  const pal = ['#36b8ff', '#1d7fd1', '#7fd4ff', '#2ef2ff', '#9db7ff', '#ff4b3e', '#c7271c', '#ff9a5a', '#ffb547', '#ff7a9a'];
  new Chart($('#c-kills'), { type: 'doughnut', data: { labels: wk.map(([k2]) => k2.replace('|', ' · ')), datasets: [{ data: wk.map(([, v]) => v),
    backgroundColor: wk.map(([k2], i) => (k2.startsWith('BLUE') ? pal[i % 5] : pal[5 + (i % 5)])), borderColor: '#121c27' }] },
    options: { animation: false, maintainAspectRatio: false, plugins: { legend: { position: 'right', labels: { boxWidth: 10, font: { size: 10 } } } } } });
  new Chart($('#c-attrition'), { type: 'bar', data: { labels: a.classes, datasets: [
    { label: 'BLUE lost', data: a.classes.map((c) => a.attrition.BLUE[c]), backgroundColor: BLUE },
    { label: 'BLUE fielded', data: a.classes.map((c) => a.fielded.BLUE[c]), backgroundColor: 'rgba(54,184,255,.25)' },
    { label: 'RED lost', data: a.classes.map((c) => a.attrition.RED[c]), backgroundColor: RED },
    { label: 'RED fielded', data: a.classes.map((c) => a.fielded.RED[c]), backgroundColor: 'rgba(255,75,62,.25)' }] },
    options: { animation: false, maintainAspectRatio: false, plugins: { legend: { labels: { boxWidth: 10 } } } } });
  $('#advice').innerHTML = a.advisor.length ? `<div class="hud-font text-muted2 mt-3">ADVISOR LOG</div>` + a.advisor.map((x) => `<div class="feed-item"><span class="mono text-muted2">${x.tick}</span> ${x.side} · ${x.kind} · ${esc(x.content.headline || x.content.q || x.content.coa || '')}</div>`).join('') : '';
}

async function killFeed() {
  const rows = await api(`battles/${BID}/kills/`, { token });
  const draw = () => {
    const f = $('#kill-filter').value.toLowerCase();
    $('#kill-rows').innerHTML = rows.filter((c) => !f || JSON.stringify(c).toLowerCase().includes(f)).map((c) => `<tr style="cursor:pointer" data-t="${c.tick}" data-v="${esc(c.victim_id)}">
      <td class="mono">${c.tick}</td><td class="${c.attacker_side === 'BLUE' ? 'side-blue' : 'side-red'}">${esc(c.attacker_name || '—')}<div class="text-muted2">${c.attacker_side || ''} ${c.attacker_formation || ''}</div></td>
      <td class="${c.victim_side === 'BLUE' ? 'side-blue' : 'side-red'}">${esc(c.victim_name)}<div class="text-muted2">${c.victim_side} ${c.victim_formation || ''} ${c.victim_cls}</div></td>
      <td>${esc(c.weapon_name || '—')}<div class="text-muted2">${esc(c.round || '')}</div></td><td class="mono">${c.range_m ?? '—'} m</td></tr>`).join('') || '<tr><td colspan="5" class="text-muted2">No casualties recorded.</td></tr>';
    document.querySelectorAll('#kill-rows [data-t]').forEach((tr) => (tr.onclick = async () => { R.playing = false; R.tick = Math.max(1, +tr.dataset.t - 20); await show(R.tick, true); const u = scene.view(tr.dataset.v); if (u) flyLocal(g, u.x, u.y - 600, 900); }));
  };
  $('#kill-filter').oninput = draw;
  draw();
}

function ask() {
  const ex = ['Which entity destroyed R-MBT-1?', 'Why did Formation ALPHA lose tanks?', 'Who had the most kills?', 'What happened to the HVTs?', 'Which weapons were most effective?', 'How many losses did each side take?'];
  $('#examples').innerHTML = ex.map((q) => `<button class="btn btn-sm btn-outline-secondary">${esc(q)}</button>`).join('');
  $('#examples').querySelectorAll('button').forEach((b) => (b.onclick = () => { $('#q').value = b.textContent; $('#ask').requestSubmit(); }));
  $('#ask').onsubmit = async (e) => {
    e.preventDefault();
    const q = $('#q').value.trim();
    if (!q) return;
    $('#chat').insertAdjacentHTML('beforeend', `<div class="align-self-end panel px-3 py-2 small">${esc(q)}</div>`);
    $('#q').value = '';
    try {
      const r = await api(`battles/${BID}/ask/`, { method: 'POST', body: { question: q }, token });
      const cites = r.citations.map((c) => `<a href="#" class="badge badge-blue text-decoration-none me-1" data-t="${c.tick}">tick ${c.tick}</a><span class="text-muted2 me-2">${esc(c.text)}</span>`).join('');
      $('#chat').insertAdjacentHTML('beforeend', `<div class="panel px-3 py-2 small"><div class="mb-1"><i class="bi bi-cpu text-amber"></i> ${esc(r.answer)}</div><div>${cites}</div><div class="text-muted2 mt-1">source: ${r.source}</div></div>`);
      $('#chat').querySelectorAll('[data-t]').forEach((a2) => (a2.onclick = (ev) => { ev.preventDefault(); R.playing = false; R.tick = Math.max(1, +a2.dataset.t - 10); show(R.tick, true); }));
    } catch (ex2) { toast(ex2.message, 'warn'); }
  };
}

async function init() {
  B = await api(`battles/${BID}/`, { token });
  $('#title').textContent = `${B.scenario.name} — ${B.theatre.name}`;
  $('#brief').textContent = B.briefing || '';
  R.last = B.last_tick;
  $('#scrub').max = R.last;
  $('#t-end').textContent = R.last;
  g = await createGlobe({ container: 'globe', origin: B.scenario.origin, halfKm: B.scenario.ao_half_km, theatre: B.theatre,
    flat: B.terrain?.meta?.source === 'flat', waterLevel: B.terrain?.meta?.water_level ?? null });
  drawZones(g, B.zones);
  drawCorridors(g, B.corridors);
  scene = new BattleScene(g, { ...B, scenario: { ...B.scenario, cadence_ms: 120 } }, { side: null, onSelect: (id) => renderSel(id) });
  const h = new Cesium.ScreenSpaceEventHandler(g.scene.canvas);
  h.setInputAction((e) => scene.select(scene.pickUnit(e.position)), Cesium.ScreenSpaceEventType.LEFT_CLICK);
  $('#play').onclick = () => { R.playing = !R.playing; if (R.tick >= R.last) R.tick = 1; $('#play').innerHTML = R.playing ? '<i class="bi bi-pause-fill"></i>' : '<i class="bi bi-play-fill"></i>'; };
  $('#fwd').onclick = () => { R.playing = false; R.tick = Math.min(R.last, Math.floor(R.tick) + 1); show(R.tick, false); };
  $('#back').onclick = () => { R.playing = false; R.tick = Math.max(1, Math.floor(R.tick) - 1); show(R.tick, true); };
  $('#speed').onchange = (e) => (R.speed = +e.target.value);
  $('#scrub').oninput = (e) => { R.tick = +e.target.value; show(R.tick, true); };
  document.querySelectorAll('[data-cam]').forEach((b) => (b.onclick = () => {
    const c = b.dataset.cam;
    if (c === 'overview') { scene.freeCam(); home(g); }
    else if (c === 'free') scene.freeCam();
    else if (c === 'follow') { if (scene.selected) scene.follow(scene.selected); else toast('Select a unit first', 'info'); }
    else { const hv = scene.cur?.units.find((u) => u.hvt && u.s === c); if (hv) { scene.freeCam(); flyLocal(g, hv.x, hv.y - 1500, 1500); } }
  }));
  if (R.last) { R.tick = 1; await show(1, true); }
  loop();
  dashboard().catch((e) => toast(e.message, 'warn'));
  killFeed().catch(() => {});
  ask();
}
init().catch((e) => toast(e.message, 'crit', 10000));
