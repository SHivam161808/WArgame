// Builds the high-detail terrain patch around the camera off the main thread.
import { makeHeightField } from './height.js';

let field = null;
self.onmessage = (e) => {
  const m = e.data;
  if (m.type === 'init') { field = makeHeightField(m.meta, new Float32Array(m.elev)); return; }
  if (m.type !== 'patch' || !field) return;
  const { cx, cy, size, res, id } = m;
  const n = Math.round(size / res) + 1;
  const pos = new Float32Array(n * n * 3);
  const nor = new Float32Array(n * n * 3);
  const h = new Float32Array(n * n);
  const half = size / 2;
  for (let j = 0; j < n; j++) for (let i = 0; i < n; i++) {
    const x = cx - half + i * res, y = cy - half + j * res;
    h[j * n + i] = field.height(x, y);
  }
  for (let j = 0; j < n; j++) for (let i = 0; i < n; i++) {
    const k = j * n + i;
    const x = cx - half + i * res, y = cy - half + j * res;
    // three.js space: X = east, Y = up, Z = -north
    pos[k * 3] = x; pos[k * 3 + 1] = h[k]; pos[k * 3 + 2] = -y;
    const hl = h[j * n + Math.max(0, i - 1)], hr = h[j * n + Math.min(n - 1, i + 1)];
    const hd = h[Math.max(0, j - 1) * n + i], hu = h[Math.min(n - 1, j + 1) * n + i];
    const dx = (hr - hl) / (2 * res), dy = (hu - hd) / (2 * res);
    // normal of z = f(x, y) is (-dx, -dy, 1) in (east, north, up) → (x, y, z) three = (-dx, 1, dy)
    const l = Math.hypot(dx, dy, 1);
    nor[k * 3] = -dx / l; nor[k * 3 + 1] = 1 / l; nor[k * 3 + 2] = dy / l;
  }
  self.postMessage({ type: 'patch', id, cx, cy, size, n, pos, nor }, [pos.buffer, nor.buffer]);
};
