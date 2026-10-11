// Shared terrain height function for the ground-level renderer (main thread + worker).
// Real elevation from the server grid (50 m) + deterministic biome micro-detail (dune ripples,
// snowdrifts, rocky ground) so the surface is sharp at tank level. Units, the terrain mesh and
// the aim ray all use the same function, so vehicles sit exactly on what you see.

export function makeHeightField(meta, elev) {
  const { cols, rows, cell_m: cell, x0, y0, biome } = meta;
  const maxI = cols - 1.0001, maxJ = rows - 1.0001;
  const grid = (x, y) => {
    let fx = (x - x0) / cell, fy = (y - y0) / cell;
    fx = fx < 0 ? 0 : fx > maxI ? maxI : fx;
    fy = fy < 0 ? 0 : fy > maxJ ? maxJ : fy;
    const i = fx | 0, j = fy | 0, tx = fx - i, ty = fy - j;
    const k = j * cols + i;
    const a = elev[k], b = elev[k + 1], c = elev[k + cols], d = elev[k + cols + 1];
    // smooth (cubic-ish) blend avoids visible facets of the 50 m grid
    const sx = tx * tx * (3 - 2 * tx), sy = ty * ty * (3 - 2 * ty);
    const lin = a + (b - a) * tx + (c - a) * ty + (a - b - c + d) * tx * ty;
    const smo = a + (b - a) * sx + (c - a) * sy + (a - b - c + d) * sx * sy;
    return lin * 0.5 + smo * 0.5;
  };
  const amp = DETAIL[biome] || DETAIL.DESERT;
  const detail = meta.detail === 0 ? () => 0 : (x, y) => amp(x, y); // village maps: the sim's exact ground
  const height = (x, y) => grid(x, y) + detail(x, y);
  return { grid, detail, height, meta };
}

// ── deterministic value noise ───────────────────────────────────────────────
function hash(ix, iy) {
  let n = (ix * 374761393 + iy * 668265263) | 0;
  n = Math.imul(n ^ (n >>> 13), 1274126177);
  return ((n ^ (n >>> 16)) >>> 0) / 4294967296;
}
export function noise(x, y) {
  const ix = Math.floor(x), iy = Math.floor(y);
  const tx = x - ix, ty = y - iy;
  const u = tx * tx * (3 - 2 * tx), v = ty * ty * (3 - 2 * ty);
  const a = hash(ix, iy), b = hash(ix + 1, iy), c = hash(ix, iy + 1), d = hash(ix + 1, iy + 1);
  return a + (b - a) * u + (c - a) * v + (a - b - c + d) * u * v;
}
export function fbm(x, y, oct = 3) {
  let s = 0, a = 0.5, f = 1;
  for (let i = 0; i < oct; i++) { s += a * noise(x * f, y * f); f *= 2.03; a *= 0.5; }
  return s;
}

const DETAIL = {
  DESERT: (x, y) => {
    const dune = (fbm(x / 70, y / 70) - 0.45) * 2.4;
    const ripple = Math.sin((x * 0.8 + y * 0.6) / 2.3 + fbm(x / 20, y / 20) * 4) * 0.07;
    return dune + ripple;
  },
  MONSOON: (x, y) => (fbm(x / 18, y / 18) - 0.5) * 0.5 + Math.sin(x / 1.6) * 0.04,
  MOUNTAIN: (x, y) => (fbm(x / 22, y / 22, 4) - 0.5) * 2.2,
  ICE: (x, y) => (fbm(x / 35, y / 35) - 0.5) * 1.2 + (fbm(x / 6, y / 6) - 0.5) * 0.15,
  MARSH: (x, y) => (fbm(x / 25, y / 25) - 0.5) * 0.35,
};
