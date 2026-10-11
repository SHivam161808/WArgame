// Local tangent plane (x east, y north, metres) <-> lon/lat. Mirrors sim/geo.py exactly.
const DEG = Math.PI / 180;
export const mPerDegLat = (lat) => { const p = lat * DEG; return 111132.92 - 559.82 * Math.cos(2 * p) + 1.175 * Math.cos(4 * p); };
export const mPerDegLon = (lat) => { const p = lat * DEG; return 111412.84 * Math.cos(p) - 93.5 * Math.cos(3 * p); };

export class LocalFrame {
  constructor(lat, lon) { this.lat = lat; this.lon = lon; this.mx = mPerDegLon(lat); this.my = mPerDegLat(lat); }
  toGeo(x, y) { return [this.lon + x / this.mx, this.lat + y / this.my]; }
  toLocal(lon, lat) { return [(lon - this.lon) * this.mx, (lat - this.lat) * this.my]; }
  cart(x, y, z = 0) { const [lon, lat] = this.toGeo(x, y); return Cesium.Cartesian3.fromDegrees(lon, lat, z); }
  fromCart(c) {
    const g = Cesium.Cartographic.fromCartesian(c);
    const [x, y] = this.toLocal(g.longitude / DEG, g.latitude / DEG);
    return [x, y, g.height];
  }
}

export const norm360 = (d) => ((d % 360) + 360) % 360;
export const angleDiff = (a, b) => { let d = (b - a) % 360; if (d > 180) d -= 360; if (d <= -180) d += 360; return d; };
export const lerp = (a, b, t) => a + (b - a) * t;
export const lerpAngle = (a, b, t) => norm360(a + angleDiff(a, b) * t);
export const bearing = (ax, ay, bx, by) => norm360(Math.atan2(bx - ax, by - ay) / DEG);
export const clamp = (v, lo, hi) => (v < lo ? lo : v > hi ? hi : v);
export const gridRef = (x, y, half) => `${String(Math.max(0, Math.floor((x + half) / 100))).padStart(3, '0')} ${String(Math.max(0, Math.floor((y + half) / 100))).padStart(3, '0')}`;
export function pointInPolygon(x, y, poly) {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i], [xj, yj] = poly[j];
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi || 1e-12) + xi) inside = !inside;
  }
  return inside;
}
export function centroid(poly) {
  const n = poly.length || 1;
  return [poly.reduce((s, p) => s + p[0], 0) / n, poly.reduce((s, p) => s + p[1], 0) / n];
}
const COMPASS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];
export const compass = (d) => COMPASS[Math.floor((norm360(d) + 22.5) / 45) % 8];
