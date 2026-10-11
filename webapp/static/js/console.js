// Command Panel (AD-1): own scenarios with live status + progress; create / open / replay / delete.
import { api, esc, toast } from './api.js';

const $ = (s) => document.querySelector(s);
const PILL = { SETUP: 'badge-amber', LOBBY: 'badge-blue', RUNNING: 'badge-green', ENDED: 'badge-red' };
let meta;

async function newSkirmish(theatre) {
  const b = $('#new-skirmish');
  if (b) { b.disabled = true; b.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Building the village…'; }
  try {
    const r = await api('skirmish/', { method: 'POST', body: { theatre } });
    localStorage.setItem(`wg_token_${r.room_code}`, r.token);
    toast(`Room ${r.room_code} · BLUE key ${r.keys.BLUE} · RED key ${r.keys.RED}`, 'ok', 8000);
    location.href = r.play_url;
  } catch (e) { toast(e.message, 'crit'); if (b) { b.disabled = false; b.innerHTML = '<i class="bi bi-house-door"></i> Village Skirmish'; } }
}
window.newSkirmish = newSkirmish;

async function load() {
  const list = await api('scenarios/');
  if (!list.length) { $('#rows').innerHTML = '<tr><td colspan="7" class="text-muted2">No scenarios yet — create one.</td></tr>'; return; }
  $('#rows').innerHTML = list.map((s) => {
    const b = s.battle;
    const prog = b ? b.progress : 0;
    const out = b && b.outcome ? ` · <b>${b.outcome}</b>` : '';
    return `<tr>
      <td><div class="fw-semibold">${esc(s.name)}</div><div class="small text-muted2">${esc(s.description || '').slice(0, 80)}</div></td>
      <td class="small">${esc(s.theatre_name)}<div class="text-muted2">${s.time_of_day} · ${s.weather}</div></td>
      <td class="mono">${s.room_code}</td>
      <td class="small mono">${s.tick_s}s · ${s.duration_min} min</td>
      <td class="small">${s.mode === 'SKIRMISH' ? '<span class="badge badge-green">SKIRMISH</span>' : s.red_mode}</td>
      <td><span class="status-pill ${PILL[s.status] || ''}">${s.status}</span> <span class="small text-muted2">${b ? `run #${b.run_no} · tick ${b.tick}${out}` : ''}</span>
        <div class="progress mt-1"><div class="progress-bar" style="width:${prog}%"></div></div></td>
      <td class="text-end text-nowrap">
        <button class="btn btn-sm btn-success hud-font" data-quick="${s.id}" title="Open a fresh lobby and join as BLUE commander"><i class="bi bi-play-fill"></i> Quick play</button>
        <a class="btn btn-sm btn-tac" href="/console/scenario/${s.id}/"><i class="bi bi-sliders"></i> Setup</a>
        <a class="btn btn-sm btn-tac ${s.status === 'SETUP' ? 'disabled' : ''}" href="/console/scenario/${s.id}/live/"><i class="bi bi-display"></i> Live</a>
        <a class="btn btn-sm btn-tac ${b && b.status === 'ENDED' ? '' : 'disabled'}" href="${b ? `/aar/${b.id}/` : '#'}"><i class="bi bi-play-btn"></i> Replay</a>
        <button class="btn btn-sm btn-outline-danger" data-del="${s.id}" data-name="${esc(s.name)}"><i class="bi bi-trash"></i></button>
      </td></tr>`;
  }).join('');
  document.querySelectorAll('[data-quick]').forEach((b) => (b.onclick = async () => {
    b.disabled = true; b.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';
    try {
      const r = await api(`scenarios/${b.dataset.quick}/quickplay/`, { method: 'POST', body: {} });
      localStorage.setItem(`wg_token_${r.room_code}`, r.token);
      toast(`Room ${r.room_code} · BLUE key ${r.keys.BLUE} · RED key ${r.keys.RED}`, 'ok', 8000);
      location.href = r.play_url;
    } catch (e) { toast(e.message, 'crit'); b.disabled = false; b.innerHTML = '<i class="bi bi-play-fill"></i> Quick play'; }
  }));
  document.querySelectorAll('[data-del]').forEach((b) => (b.onclick = async () => {
    if (!confirm(`Delete "${b.dataset.name}" and all its battles, ticks and results?`)) return;
    await api(`scenarios/${b.dataset.del}/`, { method: 'DELETE' });
    toast('Scenario deleted', 'ok');
    load();
  }));
}

async function init() {
  meta = await api('meta/');
  $('#theatre').innerHTML = meta.theatres.map((t) => `<option value="${t.id}">${esc(t.name)}</option>`).join('');
  const setWeather = () => {
    const t = meta.theatres.find((x) => x.id === $('#theatre').value);
    $('#weather').innerHTML = t.weather.map((w) => `<option ${w === t.default_weather ? 'selected' : ''} value="${w}">${meta.weather[w].label}</option>`).join('');
  };
  $('#theatre').onchange = setWeather;
  setWeather();
  $('#new-form').onsubmit = async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    const body = Object.fromEntries(f.entries());
    body.fog_enabled = f.get('fog_enabled') === 'on';
    for (const k of ['tick_s', 'duration_min', 'cadence_ms', 'ao_half_km']) body[k] = +body[k];
    try {
      const s = await api('scenarios/', { method: 'POST', body });
      location.href = `/console/scenario/${s.id}/`;
    } catch (ex) { $('#new-err').textContent = ex.message; }
  };
  await load();
  setInterval(load, 5000); // live status & progress
}
init().catch((e) => toast(e.message, 'crit'));
