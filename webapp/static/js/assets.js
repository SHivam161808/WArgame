// Asset catalogue editor (PW-1, j): edit platforms & weapons without touching source.
import { api, esc, toast } from './api.js';

const $ = (s) => document.querySelector(s);
let kind = 'platforms', rows = [], current = null, isNew = false;
const COLS = {
  platforms: [['id', 'ID'], ['name', 'Designation'], ['cls', 'Class'], ['faction', 'Force'], [(p) => `${p.armour.front}/${p.armour.side}/${p.armour.rear}/${p.armour.top}`, 'Armour F/S/R/T mm'],
    [(p) => `${p.mass_t} t · ${p.power_kw} kW`, 'Mass · power'], [(p) => `${p.road_kph}/${p.offroad_kph}${p.swim_kph ? `/${p.swim_kph}` : ''}`, 'Road/off/swim km/h'], ['max_grade_deg', 'Grade °'],
    [(p) => `${p.fuel_l} L · ${p.fuel_l_per_km} L/km`, 'Fuel'], [(p) => `${p.sensor.range_m} m${p.sensor.thermal ? ' TI' : ''}${p.sensor.radar ? ' R' : ''}`, 'Sensor'], ['crew_skill', 'Crew'], [(p) => p.weapons.join(', '), 'Weapons']],
  weapons: [['id', 'ID'], ['name', 'Name'], ['type', 'Type'], ['round', 'Round'], ['range_m', 'Range m'], ['dispersion_mrad', 'Disp. mrad'], ['muzzle_vel', 'V0 m/s'], ['pen_mm', 'Pen mm'],
    ['damage', 'Damage'], ['blast_m', 'Blast m'], ['ammo', 'Ammo'], ['reload_s', 'Reload s'], [(w) => [w.guided && 'guided', w.top_attack && 'top-attack', w.indirect && 'indirect', w.vs_air && 'AA'].filter(Boolean).join(' '), 'Traits']],
};
const FIELDS = {
  platforms: [['name', 'text'], ['cls', 'text'], ['faction', 'text'], ['mass_t', 'number'], ['power_kw', 'number'], ['road_kph', 'number'], ['offroad_kph', 'number'], ['swim_kph', 'number'],
    ['ford_depth_m', 'number'], ['max_grade_deg', 'number'], ['turn_rate_dps', 'number'], ['fuel_l', 'number'], ['fuel_l_per_km', 'number'], ['crew_skill', 'number'], ['hp', 'number'], ['signature', 'number'], ['points', 'number']],
  weapons: [['name', 'text'], ['type', 'text'], ['round', 'text'], ['range_m', 'number'], ['min_range_m', 'number'], ['dispersion_mrad', 'number'], ['muzzle_vel', 'number'], ['pen_mm', 'number'],
    ['damage', 'number'], ['blast_m', 'number'], ['ammo', 'number'], ['reload_s', 'number'], ['salvo', 'number']],
};
const cell = (r, c) => (typeof c === 'function' ? c(r) : r[c]);

async function load() {
  rows = await api(`assets/${kind}/`);
  $('#head').innerHTML = `<tr>${COLS[kind].map((c) => `<th>${c[1]}</th>`).join('')}</tr>`;
  $('#rows').innerHTML = rows.map((r, i) => `<tr style="cursor:pointer" data-i="${i}">${COLS[kind].map((c) => `<td>${esc(cell(r, c[0]))}</td>`).join('')}</tr>`).join('');
  document.querySelectorAll('[data-i]').forEach((tr) => (tr.onclick = () => open(rows[+tr.dataset.i], false)));
}
function open(rec, fresh) {
  current = structuredClone(rec);
  isNew = fresh;
  delete current.updated;
  $('#edit-title').textContent = fresh ? `NEW ${kind.slice(0, -1).toUpperCase()}` : `${current.id} — ${current.name}`;
  $('#form').innerHTML = (fresh ? `<div class="mb-2"><label class="form-label small">ID</label><input class="form-control form-control-sm" id="f-id" value="${esc(current.id)}"></div>` : '') +
    `<div class="row g-2">${FIELDS[kind].map(([k, t]) => `<div class="col-6 col-md-4"><label class="form-label small">${k}</label><input class="form-control form-control-sm" data-k="${k}" type="${t}" step="any" value="${esc(current[k])}"></div>`).join('')}</div>`;
  $('#json').value = JSON.stringify(current, null, 2);
  $('#form').querySelectorAll('[data-k]').forEach((inp) => (inp.oninput = () => {
    const obj = JSON.parse($('#json').value);
    obj[inp.dataset.k] = inp.type === 'number' ? +inp.value : inp.value;
    $('#json').value = JSON.stringify(obj, null, 2);
  }));
  $('#del').style.display = fresh ? 'none' : '';
  $('#edit-err').textContent = '';
  bootstrap.Modal.getOrCreateInstance($('#edit')).show();
}
$('#save').onclick = async () => {
  try {
    const body = JSON.parse($('#json').value);
    if (isNew) { body.id = $('#f-id').value.trim(); await api(`assets/${kind}/`, { method: 'POST', body }); }
    else await api(`assets/${kind}/${current.id}/`, { method: 'PUT', body });
    bootstrap.Modal.getInstance($('#edit')).hide();
    toast('Saved — applies from the next battle start', 'ok');
    load();
  } catch (e) { $('#edit-err').textContent = e.message; }
};
$('#del').onclick = async () => { if (!confirm(`Delete ${current.id}?`)) return; await api(`assets/${kind}/${current.id}/`, { method: 'DELETE' }); bootstrap.Modal.getInstance($('#edit')).hide(); load(); };
$('#add').onclick = () => { const base = structuredClone(rows[0] || {}); base.id = `custom-${Date.now() % 100000}`; base.name = 'New asset'; open(base, true); };
$('#reset').onclick = async () => { if (!confirm('Reset the whole catalogue to defaults?')) return; await api('assets/reset/', { method: 'POST', body: {} }); toast('Catalogue reset', 'ok'); load(); };
document.querySelectorAll('[data-kind]').forEach((b) => (b.onclick = () => { document.querySelectorAll('[data-kind]').forEach((x) => x.classList.toggle('active', x === b)); kind = b.dataset.kind; load(); }));
load().catch((e) => toast(e.message, 'crit'));
