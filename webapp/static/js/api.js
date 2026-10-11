// REST + WebSocket helpers shared by every page.
export function csrf() {
  const m = document.cookie.match(/csrftoken=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : '';
}

export async function api(path, { method = 'GET', body, token, raw } = {}) {
  const headers = { 'X-CSRFToken': csrf() };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (token) headers['X-Player-Token'] = token;
  const res = await fetch(path.startsWith('/') ? path : `/api/${path}`, {
    method, headers, credentials: 'same-origin', body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (raw) return res;
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || data.detail || `HTTP ${res.status}`);
  return data;
}

export function toast(text, level = 'info', ms = 4500) {
  const box = document.getElementById('toasts');
  if (!box) return;
  const cls = { info: 'text-bg-primary', warn: 'text-bg-warning', crit: 'text-bg-danger', ok: 'text-bg-success' }[level] || 'text-bg-secondary';
  const el = document.createElement('div');
  el.className = `toast align-items-center border-0 ${cls}`;
  el.innerHTML = `<div class="d-flex"><div class="toast-body"></div><button class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button></div>`;
  el.querySelector('.toast-body').textContent = text;
  box.appendChild(el);
  const t = new bootstrap.Toast(el, { delay: ms });
  t.show();
  el.addEventListener('hidden.bs.toast', () => el.remove());
}

export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

/** WebSocket client with request/response (rid), auto-reconnect and typed handlers. */
export class Socket {
  constructor(roomCode, onOpen) {
    this.url = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/battle/${roomCode}/`;
    this.handlers = {};
    this.pending = new Map();
    this.rid = 0;
    this.onOpen = onOpen;
    this.closed = false;
    this.connect();
  }
  connect() {
    this.ws = new WebSocket(this.url);
    this.ws.onopen = () => { this.retry = 0; this.onOpen && this.onOpen(); this.emit('_open'); };
    this.ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.type === 'reply') {
        const p = this.pending.get(msg.rid);
        if (p) { this.pending.delete(msg.rid); msg.ok ? p.resolve(msg) : p.reject(new Error(msg.error || 'request failed')); }
        return;
      }
      this.emit(msg.type, msg);
    };
    this.ws.onclose = () => {
      this.emit('_close');
      for (const p of this.pending.values()) p.reject(new Error('connection lost'));
      this.pending.clear();
      if (!this.closed) setTimeout(() => this.connect(), Math.min(8000, 500 * 2 ** (this.retry = (this.retry || 0) + 1)));
    };
  }
  on(type, fn) { (this.handlers[type] ||= []).push(fn); return this; }
  emit(type, msg) { for (const fn of this.handlers[type] || []) fn(msg); }
  send(type, data = {}) { if (this.ws.readyState === 1) this.ws.send(JSON.stringify({ type, ...data })); }
  request(type, data = {}) {
    return new Promise((resolve, reject) => {
      if (this.ws.readyState !== 1) return reject(new Error('not connected'));
      const rid = ++this.rid;
      this.pending.set(rid, { resolve, reject });
      this.ws.send(JSON.stringify({ type, rid, ...data }));
      setTimeout(() => { if (this.pending.has(rid)) { this.pending.delete(rid); reject(new Error('timeout')); } }, 20000);
    });
  }
  close() { this.closed = true; this.ws.close(); }
}
