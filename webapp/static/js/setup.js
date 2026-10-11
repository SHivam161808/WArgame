// Scenario setup (AD-3): terrain, zones (vertices / lasso), inventory, readiness, force keys, replay.
import { api, esc, toast } from './api.js';
import { createGlobe, drawCorridors, drawZones, home, pickLocal, search, toggle2D } from './globe.js';

const C = Cesium;
const $ = (s) => document.querySelector(s);
const SID = $('#app').dataset.sid;
let sc, meta, g, zones = {};

async function refresh() {
  sc = await api(`scenarios/${SID}/`);
  $('#status').textContent = sc.status;
  zones = sc.zones || {};
  return sc;
}

// ── terrain ────────────────────────────────────────────────────────────────
function renderTerrain() {
  const t = meta.theatres.find((x) => x.id === sc.theatre);
  $('#theatre-info').innerHTML = `<div class="hud-font fw-bold">${esc(t.name)}</div><div class="text-muted2">${esc(t.region)} · ${t.season} · ${t.biome}</div>
    <div class="mt-1">${esc(t.description)}</div><div class="text-muted2 mt-1 mono">origin ${t.lat.toFixed(4)}, ${t.lon.toFixed(4)}</div>`;
  $('#tod').value = sc.time_of_day;
  $('#weather').innerHTML = Object.entries(meta.weather).map(([k, w]) => `<option value="${k}" ${k === sc.weather ? 'selected' : ''}>${w.label}${t.weather.includes(k) ? '' : ' (atypical)'}</option>`).join('');
  $('#cell').value = sc.cell_m;
  $('#ao').value = sc.ao_half_km;
  const tr = sc.terrain;
  if (!tr) { $('#terrain-stats').innerHTML = '<span class="text-warning">Terrain not ingested yet.</span>'; $('#layer-img').style.display = 'none'; return; }
  const st = tr.stats;
  $('#terrain-stats').innerHTML = `<div class="${tr.meta.source === 'flat' ? 'text-warning' : 'text-success'}"><i class="bi bi-check-circle"></i> ${esc(tr.note)}</div>
    <table class="table table-sm mt-2 mb-1"><tbody>
    <tr><td>Grid</td><td class="mono">${tr.meta.cols} × ${tr.meta.rows} cells @ ${tr.meta.cell_m} m</td></tr>
    <tr><td>Elevation</td><td class="mono">${st.min_elev} – ${st.max_elev} m (mean ${st.mean_elev})</td></tr>
    <tr><td>Mean slope</td><td class="mono">${st.mean_slope}°</td></tr>
    <tr><td>Flat / sloped / steep</td><td class="mono">${st.pct_flat}% / ${st.pct_sloped}% / ${st.pct_steep}%</td></tr>
    <tr><td>Open water</td><td class="mono">${st.pct_water}%</td></tr>
    <tr><td>Ground</td><td class="mono">${Object.entries(st.ground).map(([k, v]) => `${k.toLowerCase()} ${v}%`).join(', ')}</td></tr>
    <tr><td>Avenues</td><td class="mono">${tr.corridors.length}</td></tr></tbody></table>`;
  $('#layer-img').style.display = '';
  $('#layer-img').src = `/api/scenarios/${SID}/terrain/layer.png?layer=${document.querySelector('#layer-btns .active').dataset.layer}&t=${Date.now()}`;
}

async function saveSettings(patch) {
  try { sc = { ...sc, ...(await api(`scenarios/${SID}/`, { method: 'PATCH', body: patch })) }; toast('Saved', 'ok', 1200); }
  catch (e) { toast(e.message, 'warn'); }
}

// ── zones: vertices & lasso (TR-2) ─────────────────────────────────────────
let draw = null;
function simplify(pts, tol) {
  if (pts.length < 3) return pts;
  const sq = (a, b, p) => {
    const dx = b[0] - a[0], dy = b[1] - a[1];
    const t = Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy || 1)));
    return Math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy);
  };
  const rec = (a, b) => {
    let idx = -1, max = 0;
    for (let i = a + 1; i < b; i++) { const d = sq(pts[a], pts[b], pts[i]); if (d > max) { max = d; idx = i; } }
    return max > tol ? [...rec(a, idx).slice(0, -1), ...rec(idx, b)] : [pts[a], pts[b]];
  };
  return rec(0, pts.length - 1);
}
function startDraw(side, method) {
  draw = { side, method, pts: [], down: false };
  $('#draw-banner').style.display = '';
  $('#draw-banner').textContent = method === 'lasso' ? `${side} zone — press and drag to outline, release to finish · Esc cancels` : `${side} zone — click vertices, double-click to finish · Esc cancels`;
  g.scene.screenSpaceCameraController.enableInputs = method !== 'lasso';
}
function preview() {
  if (draw.ent) g.viewer.entities.remove(draw.ent);
  if (draw.pts.length < 2) return;
  const col = draw.side === 'BLUE' ? C.Color.fromCssColorString('#36b8ff') : C.Color.fromCssColorString('#ff4b3e');
  const pos = draw.pts.map(([x, y]) => g.frame.cart(x, y, 0));
  draw.ent = g.viewer.entities.add({ polyline: { positions: [...pos, pos[0]], width: 3, material: col, clampToGround: true } });
}
async function finishDraw(ok) {
  const d = draw;
  draw = null;
  $('#draw-banner').style.display = 'none';
  g.scene.screenSpaceCameraController.enableInputs = true;
  if (d.ent) g.viewer.entities.remove(d.ent);
  if (!ok) return;
  let pts = d.method === 'lasso' ? simplify([...d.pts, d.pts[0]], 60).slice(0, -1) : d.pts;
  if (pts.length < 3) { toast('A zone needs at least 3 points', 'warn'); return; }
  try {
    const r = await api(`scenarios/${SID}/zones/${d.side}/`, { method: 'PUT', body: { polygon: pts, method: d.method } });
    zones[d.side] = r.polygon;
    drawZones(g, Object.entries(zones).map(([side, polygon]) => ({ side, polygon })));
    if (r.corridors) drawCorridors(g, r.corridors);
    renderZoneInfo(r.corridors);
    toast(`${d.side} zone saved — avenues of approach regenerated`, 'ok');
  } catch (e) { toast(e.message, 'warn'); }
}
function renderZoneInfo(corridors) {
  for (const side of ['BLUE', 'RED']) $(`#z-${side}`).textContent = zones[side] ? `${zones[side].length} vertices` : 'not drawn';
  const cs = corridors || sc.terrain?.corridors || [];
  $('#corridor-info').innerHTML = cs.length ? `<i class="bi bi-signpost-split text-amber"></i> ${cs.length} mobility corridors between the zones (road-like going within ~140 m).` : '<span class="text-muted2">Corridors appear once terrain and both zones exist.</span>';
}

// ── inventory ──────────────────────────────────────────────────────────────
async function renderInventory() {
  const plats = (await api('assets/platforms/')).filter((p) => p.cls !== 'HVT');
  const inv = sc.inventory;
  for (const side of ['BLUE', 'RED']) $(`#unr-${side}`).checked = inv[side] === null;
  $('#inv-rows').innerHTML = plats.map((p) => `<tr><td>${esc(p.name)}</td><td class="text-muted2">${p.cls}</td>
    ${['BLUE', 'RED'].map((side) => `<td><input class="form-control form-control-sm" style="width:64px" type="number" min="0" max="99" data-side="${side}" data-p="${p.id}" value="${inv[side]?.[p.id] ?? 0}"></td>`).join('')}</tr>`).join('');
  const sync = () => { for (const side of ['BLUE', 'RED']) document.querySelectorAll(`[data-side=${side}]`).forEach((i) => (i.disabled = $(`#unr-${side}`).checked)); };
  $('#unr-BLUE').onchange = sync; $('#unr-RED').onchange = sync; sync();
}
async function saveInventory() {
  const body = {};
  for (const side of ['BLUE', 'RED']) {
    if ($(`#unr-${side}`).checked) { body[side] = null; continue; }
    body[side] = {};
    document.querySelectorAll(`[data-side=${side}]`).forEach((i) => { if (+i.value > 0) body[side][i.dataset.p] = +i.value; });
  }
  try { sc.inventory = await api(`scenarios/${SID}/inventory/`, { method: 'PUT', body }); toast('Inventory saved', 'ok'); } catch (e) { toast(e.message, 'warn'); }
}

// ── readiness, keys, replay ───────────────────────────────────────────────
async function renderReadiness() {
  const r = await api(`scenarios/${SID}/readiness/`);
  $('#checks').innerHTML = r.checks.map((c) => `<div class="check-row"><i class="bi ${c.ok ? 'bi-check-circle-fill text-success' : 'bi-circle text-warning'}"></i>
    <div><div>${esc(c.label)}</div><div class="small text-muted2">${esc(c.detail)}</div></div></div>`).join('');
  $('#open').disabled = !r.can_open;
  $('#ai-blue').checked = sc.allow_ai_blue;
  $('#briefing').textContent = sc.briefing || '';
}
async function renderKeys() {
  const k = await api(`scenarios/${SID}/keys/`);
  $('#red-mode').value = k.red_mode;
  $('#k-room').textContent = k.room_code;
  $('#k-BLUE').textContent = k.keys.BLUE || '—';
  $('#k-RED').textContent = k.keys.RED || '—';
  $('#k-url').textContent = `${location.origin}/?room=${k.room_code}`;
}
async function renderRuns() {
  const runs = await api(`scenarios/${SID}/battles/`);
  $('#runs').innerHTML = runs.length ? `<table class="table table-sm"><thead><tr><th>Run</th><th>Status</th><th>Outcome</th><th>Tick</th><th></th></tr></thead><tbody>${runs.map((b) => `<tr><td>#${b.run_no}</td><td>${b.status}</td><td>${b.outcome || '—'}</td><td class="mono">${b.tick}</td>
    <td>${b.status === 'ENDED' ? `<a class="btn btn-sm btn-tac" href="/aar/${b.id}/">Replay &amp; results</a>` : ''}</td></tr>`).join('')}</tbody></table>` : '<span class="text-muted2">No runs yet — the replay link appears once a battle completes.</span>';
}

// ── wiring ─────────────────────────────────────────────────────────────────
async function init() {
  meta = await api('meta/');
  await refresh();
  g = await createGlobe({ container: 'globe', origin: sc.origin, halfKm: sc.ao_half_km, theatre: meta.theatres.find((t) => t.id === sc.theatre),
    flat: sc.terrain?.meta?.source === 'flat', waterLevel: sc.terrain?.meta?.water_level ?? null });
  drawZones(g, Object.entries(zones).map(([side, polygon]) => ({ side, polygon })));
  drawCorridors(g, sc.terrain?.corridors || []);
  renderTerrain(); renderZoneInfo(); renderInventory(); renderReadiness(); renderKeys(); renderRuns();

  const h = new C.ScreenSpaceEventHandler(g.scene.canvas);
  h.setInputAction(async (e) => {
    const p = pickLocal(g, e.position);
    if (!p) return;
    if (draw && draw.method === 'vertices') { draw.pts.push([p[0], p[1]]); preview(); return; }
    if (!draw && sc.terrain) {
      const r = await api(`scenarios/${SID}/terrain/sample/?x=${p[0].toFixed(0)}&y=${p[1].toFixed(0)}`);
      $('#probe').innerHTML = `grid ${r.grid} · elev ${r.elevation} m · slope ${r.slope_deg}° (${r.terrain_class}) · ${r.ground} · mobility ${r.mobility} · conceal ${r.concealment}${r.water_depth ? ` · water ${r.water_depth} m` : ''}${r.on_corridor ? ' · corridor' : ''}`;
    }
  }, C.ScreenSpaceEventType.LEFT_CLICK);
  h.setInputAction(() => { if (draw?.method === 'vertices') finishDraw(true); }, C.ScreenSpaceEventType.LEFT_DOUBLE_CLICK);
  h.setInputAction((e) => { if (draw?.method === 'lasso') { draw.down = true; const p = pickLocal(g, e.position); if (p) draw.pts = [[p[0], p[1]]]; } }, C.ScreenSpaceEventType.LEFT_DOWN);
  h.setInputAction((e) => {
    if (draw?.method !== 'lasso' || !draw.down) return;
    const p = pickLocal(g, e.endPosition);
    const last = draw.pts[draw.pts.length - 1];
    if (p && (!last || Math.hypot(p[0] - last[0], p[1] - last[1]) > 80)) { draw.pts.push([p[0], p[1]]); preview(); }
  }, C.ScreenSpaceEventType.MOUSE_MOVE);
  h.setInputAction(() => { if (draw?.method === 'lasso' && draw.down) finishDraw(true); }, C.ScreenSpaceEventType.LEFT_UP);
  window.addEventListener('keydown', (e) => { if (e.key === 'Escape' && draw) finishDraw(false); });

  document.querySelectorAll('[data-draw]').forEach((b) => (b.onclick = () => startDraw(b.dataset.draw, b.dataset.method)));
  document.querySelectorAll('[data-erase]').forEach((b) => (b.onclick = async () => {
    if (!confirm(`Erase the ${b.dataset.erase} zone?`)) return;
    await api(`scenarios/${SID}/zones/${b.dataset.erase}/`, { method: 'DELETE' });
    delete zones[b.dataset.erase];
    drawZones(g, Object.entries(zones).map(([side, polygon]) => ({ side, polygon })));
    renderZoneInfo([]);
  }));
  document.querySelectorAll('#layer-btns button').forEach((b) => (b.onclick = () => {
    document.querySelectorAll('#layer-btns button').forEach((x) => x.classList.toggle('active', x === b));
    renderTerrain();
  }));
  const ingest = async (mode) => {
    const btn = mode === 'flat' ? $('#ingest-flat') : $('#ingest');
    btn.disabled = true; btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Ingesting…';
    try { const r = await api(`scenarios/${SID}/terrain/ingest/`, { method: 'POST', body: { mode } }); toast(r.note, r.fallback ? 'warn' : 'ok'); await refresh(); renderTerrain(); drawCorridors(g, sc.terrain.corridors); renderZoneInfo(); renderReadiness(); }
    catch (e) { toast(e.message, 'crit'); }
    btn.disabled = false; btn.innerHTML = mode === 'flat' ? 'Flat fallback' : '<i class="bi bi-cloud-download"></i> Ingest real terrain';
  };
  $('#ingest').onclick = () => ingest('real');
  $('#ingest-flat').onclick = () => ingest('flat');
  $('#tod').onchange = (e) => saveSettings({ time_of_day: e.target.value });
  $('#weather').onchange = (e) => saveSettings({ weather: e.target.value });
  $('#cell').onchange = (e) => saveSettings({ cell_m: +e.target.value }).then(() => toast('Re-ingest terrain to apply the new grid', 'info'));
  $('#ao').onchange = (e) => saveSettings({ ao_half_km: +e.target.value }).then(() => toast('Re-ingest terrain to apply the new AO', 'info'));
  $('#inv-save').onclick = saveInventory;
  $('#ai-blue').onchange = (e) => saveSettings({ allow_ai_blue: e.target.checked });
  $('#gen-keys').onclick = async () => { await api(`scenarios/${SID}/keys/`, { method: 'POST', body: { red_mode: $('#red-mode').value } }); toast('New force keys generated (old keys revoked)', 'ok'); renderKeys(); renderReadiness(); };
  $('#open').onclick = async () => { try { const r = await api(`scenarios/${SID}/open/`, { method: 'POST', body: {} }); toast(`Lobby open — room ${r.room_code}`, 'ok'); await refresh(); renderReadiness(); renderRuns(); } catch (e) { toast(e.message, 'warn'); } };
  $('#close').onclick = async () => { await api(`scenarios/${SID}/close/`, { method: 'POST', body: {} }); await refresh(); renderReadiness(); toast('Room closed', 'ok'); };
  $('#brief').onclick = async () => { $('#briefing').textContent = 'Generating…'; const r = await api(`scenarios/${SID}/briefing/`, { method: 'POST', body: {} }); sc.briefing = r.briefing; $('#briefing').textContent = r.briefing; };
  document.querySelectorAll('[data-copy]').forEach((b) => (b.onclick = () => { navigator.clipboard.writeText($('#' + b.dataset.copy).textContent); toast('Copied', 'ok', 1000); }));
  $('#m-home').onclick = () => home(g);
  $('#m-2d').onclick = () => toggle2D(g);
  $('#m-go').onclick = () => search(g, $('#m-search').value).catch((e) => toast(e.message, 'warn'));
  document.querySelector('[data-bs-target="#t-ready"]').addEventListener('shown.bs.tab', renderReadiness);
  document.querySelector('[data-bs-target="#t-replay"]').addEventListener('shown.bs.tab', renderRuns);
}
init().catch((e) => toast(e.message, 'crit', 10000));
