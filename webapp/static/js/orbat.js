// Force hierarchy register (o, OB-1).
import { api, esc, toast } from './api.js';

const $ = (s) => document.querySelector(s);
const ICON = { LAND: 'bi-globe-asia-australia', FORCE: 'bi-collection', UNIT: 'bi-people', SUBUNIT: 'bi-person-badge' };
const CHILD = { LAND: 'Force', FORCE: 'Unit', UNIT: 'SubUnit' };
let nodes = [];

async function load() {
  const r = await api('orbat/');
  nodes = r.nodes;
  $('#totals').innerHTML = Object.entries(r.totals).map(([k, v]) => `<div class="col-6 col-md-3"><div class="kpi"><div class="v">${v}</div><div class="k">${k === 'SUBUNIT' ? 'SubUnits' : k[0] + k.slice(1).toLowerCase() + 's'}</div></div></div>`).join('');
  render();
}
function row(n, depth) {
  return `<div class="d-flex align-items-center gap-2 py-1 border-bottom" style="border-color:var(--line)!important;padding-left:${depth * 22}px">
    <i class="bi ${ICON[n.level]} text-blue"></i><span class="fw-semibold">${esc(n.name)}</span>
    <span class="badge badge-blue">${n.level}</span>${n.typology ? `<span class="small text-muted2">${esc(n.typology)}</span>` : ''}
    <span class="small text-muted2">${n.children ? `${n.children} ${CHILD[n.level] || ''}${n.children > 1 ? 's' : ''}` : ''}</span>
    <span class="ms-auto">${CHILD[n.level] ? `<button class="btn btn-sm btn-tac" data-add="${n.id}"><i class="bi bi-plus"></i> ${CHILD[n.level]}</button>` : ''}
    <button class="btn btn-sm btn-outline-secondary" data-ren="${n.id}"><i class="bi bi-pencil"></i></button>
    <button class="btn btn-sm btn-outline-danger" data-del="${n.id}"><i class="bi bi-trash"></i></button></span></div>`;
}
function render() {
  const view = $('#view').value;
  let html = '';
  if (view === 'TREE') {
    const walk = (pid, d) => nodes.filter((n) => n.parent_id === pid).forEach((n) => { html += row(n, d); walk(n.id, d + 1); });
    walk(null, 0);
  } else nodes.filter((n) => n.level === view).forEach((n) => { html += row(n, 0); });
  $('#tree').innerHTML = html || '<span class="text-muted2">Empty register.</span>';
  document.querySelectorAll('[data-add]').forEach((b) => (b.onclick = () => add(b.dataset.add)));
  document.querySelectorAll('[data-ren]').forEach((b) => (b.onclick = async () => {
    const n = nodes.find((x) => x.id === b.dataset.ren);
    const name = prompt('Name', n.name); if (!name) return;
    const typ = n.level === 'FORCE' ? prompt('Typology (e.g. Armoured Corps)', n.typology || '') : n.typology;
    try { await api(`orbat/${n.id}/`, { method: 'PATCH', body: { name, typology: typ || '' } }); load(); } catch (e) { toast(e.message, 'warn'); }
  }));
  document.querySelectorAll('[data-del]').forEach((b) => (b.onclick = async () => {
    if (!confirm('Delete this node and everything below it?')) return;
    await api(`orbat/${b.dataset.del}/`, { method: 'DELETE' }); load();
  }));
}
async function add(parent) {
  const name = prompt('Name'); if (!name) return;
  const p = nodes.find((n) => n.id === parent);
  const typology = !p || p.level === 'LAND' ? prompt('Typology (e.g. Armoured Corps, Artillery, Army Aviation)', '') : '';
  try { await api('orbat/', { method: 'POST', body: { name, parent_id: parent || null, typology: typology || '' } }); load(); } catch (e) { toast(e.message, 'warn'); }
}
$('#add-land').onclick = () => add(null);
$('#view').onchange = render;
load().catch((e) => toast(e.message, 'crit'));
