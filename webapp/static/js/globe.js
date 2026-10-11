// CesiumJS globe on free/open data only (C-1): AWS Terrain Tiles (terrarium) elevation,
// EOX Sentinel-2 cloudless imagery, OpenStreetMap, Natural Earth borders (India POV + LoC).
import { LocalFrame, gridRef } from './geo.js';

const TERRARIUM = 'https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png';
const DEG = Math.PI / 180;

/** Heightmap terrain provider decoding terrarium PNG tiles (Web Mercator) onto Cesium's geographic tiles. */
export function terrariumProvider({ maxZoom = 14, size = 33 } = {}) {
  const cache = new Map();
  const order = [];
  async function tile(z, x, y) {
    const key = `${z}/${x}/${y}`;
    if (cache.has(key)) return cache.get(key);
    const p = (async () => {
      const n = 2 ** z;
      if (y < 0 || y >= n) return new Float32Array(256 * 256);
      const url = TERRARIUM.replace('{z}', z).replace('{x}', ((x % n) + n) % n).replace('{y}', y);
      const res = await fetch(url, { mode: 'cors' });
      if (!res.ok) throw new Error('tile');
      const bmp = await createImageBitmap(await res.blob());
      const c = new OffscreenCanvas(256, 256);
      const g = c.getContext('2d', { willReadFrequently: true });
      g.drawImage(bmp, 0, 0);
      const d = g.getImageData(0, 0, 256, 256).data;
      const h = new Float32Array(256 * 256);
      for (let i = 0; i < h.length; i++) h[i] = d[i * 4] * 256 + d[i * 4 + 1] + d[i * 4 + 2] / 256 - 32768;
      return h;
    })();
    cache.set(key, p);
    order.push(key);
    if (order.length > 600) cache.delete(order.shift());
    p.catch(() => cache.delete(key));
    return p;
  }
  const lon2px = (lon, z) => ((lon + 180) / 360) * 256 * 2 ** z;
  const lat2px = (lat, z) => { const s = Math.sin(lat * DEG); return (0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)) * 256 * 2 ** z; };
  const scheme = new Cesium.GeographicTilingScheme();
  return new Cesium.CustomHeightmapTerrainProvider({
    width: size, height: size, tilingScheme: scheme,
    callback: async (x, y, level) => {
      const z = Math.min(maxZoom, level + 1);
      const r = scheme.tileXYToRectangle(x, y, level);
      const out = new Float32Array(size * size);
      const west = r.west / DEG, east = r.east / DEG, north = r.north / DEG, south = r.south / DEG;
      if (north > 85 || south < -85) return out;
      const tiles = new Map();
      const need = [];
      for (let j = 0; j < size; j++) for (let i = 0; i < size; i++) {
        const lon = west + ((east - west) * i) / (size - 1);
        const lat = north - ((north - south) * j) / (size - 1);
        const px = lon2px(lon, z) - 0.5, py = lat2px(lat, z) - 0.5;
        need.push([px, py]);
        for (const [tx, ty] of [[Math.floor(px / 256), Math.floor(py / 256)], [Math.floor((px + 1) / 256), Math.floor((py + 1) / 256)]]) {
          const k = `${tx}/${ty}`;
          if (!tiles.has(k)) tiles.set(k, tile(z, tx, ty).catch(() => null));
        }
      }
      const loaded = new Map();
      for (const [k, p] of tiles) loaded.set(k, await p);
      const get = (gx, gy) => {
        const t = loaded.get(`${Math.floor(gx / 256)}/${Math.floor(gy / 256)}`);
        if (!t) return 0;
        return t[(((gy % 256) + 256) % 256) * 256 + (((gx % 256) + 256) % 256)];
      };
      for (let k = 0; k < need.length; k++) {
        const [px, py] = need[k];
        const ix = Math.floor(px), iy = Math.floor(py), tx = px - ix, ty = py - iy;
        const v = get(ix, iy) * (1 - tx) * (1 - ty) + get(ix + 1, iy) * tx * (1 - ty) + get(ix, iy + 1) * (1 - tx) * ty + get(ix + 1, iy + 1) * tx * ty;
        out[k] = v > -500 ? v : 0;
      }
      return out;
    },
  });
}

export const IMAGERY = {
  s2: () => new Cesium.UrlTemplateImageryProvider({
    url: 'https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless_3857/default/g/{z}/{y}/{x}.jpg',
    tilingScheme: new Cesium.WebMercatorTilingScheme(), maximumLevel: 15,
    credit: 'Sentinel-2 cloudless 2016 by EOX (CC BY 4.0), contains modified Copernicus Sentinel data',
  }),
  osm: () => new Cesium.OpenStreetMapImageryProvider({ url: 'https://tile.openstreetmap.org/', credit: '© OpenStreetMap contributors (ODbL)' }),
};

/**
 * Create the battle globe.
 * opts: { container, origin: {lat, lon}, halfKm, theatre, flat, waterLevel, lowPower }
 */
export async function createGlobe(opts) {
  Cesium.Ion.defaultAccessToken = undefined;
  const frame = new LocalFrame(opts.origin.lat, opts.origin.lon);
  const viewer = new Cesium.Viewer(opts.container, {
    baseLayer: Cesium.ImageryLayer.fromProviderAsync(Promise.resolve(IMAGERY.s2())),
    terrainProvider: opts.flat ? new Cesium.EllipsoidTerrainProvider() : terrariumProvider(),
    baseLayerPicker: false, geocoder: false, homeButton: false, sceneModePicker: false, navigationHelpButton: false,
    animation: false, timeline: false, fullscreenButton: false, infoBox: false, selectionIndicator: false,
    shouldAnimate: true, msaaSamples: opts.lowPower ? 1 : 2, useBrowserRecommendedResolution: false,
    contextOptions: { webgl: { powerPreference: 'high-performance', alpha: false } },
  });
  const scene = viewer.scene;
  scene.globe.depthTestAgainstTerrain = true;
  scene.globe.enableLighting = true;
  scene.globe.maximumScreenSpaceError = opts.lowPower ? 3 : 2; // terrain/imagery sharpness
  scene.globe.tileCacheSize = 400;
  scene.globe.preloadSiblings = true;
  scene.fog.enabled = true;
  scene.highDynamicRange = false;
  scene.postProcessStages.fxaa.enabled = true;
  scene.skyAtmosphere.show = true;
  viewer.resolutionScale = Math.min(window.devicePixelRatio || 1, opts.lowPower ? 1 : 2);
  viewer.cesiumWidget.creditContainer.style.display = 'none';
  const halfM = opts.halfKm * 1000;
  const g = {
    viewer, scene, frame, halfM,
    layers: { zones: [], corridors: [], annotations: [] },
    cart: (x, y, z = 0) => frame.cart(x, y, z),
  };

  g.overlays = {};
  g.mapState = { zones: [], corridors: [], show: { zones: true, corridors: true, grid: true, borders: true } };
  if (opts.waterLevel != null) addWater(g, opts.waterLevel);
  const dbg = new URLSearchParams(location.search).get('debug') || '';
  g.debug = dbg;
  if (!dbg.includes('nooverlay')) { loadBorders(g).catch((e) => console.warn('borders', e)); buildGrid(g); }
  if (!dbg.includes('noquality')) adaptiveQuality(g, opts.lowPower);
  if (dbg.includes('nolight')) { scene.globe.enableLighting = false; }
  home(g, 0.9);
  return g;
}

export function home(g, duration = 1.5) {
  const { frame, halfM } = g;
  const [lon, lat] = frame.toGeo(0, -halfM * 1.55);
  g.viewer.camera.flyTo({
    destination: Cesium.Cartesian3.fromDegrees(lon, lat, halfM * 1.45),
    orientation: { heading: 0, pitch: -38 * DEG, roll: 0 }, duration,
  });
}

function addWater(g, level) {
  const { frame, halfM } = g;
  const [w, s] = frame.toGeo(-halfM, -halfM), [e, n] = frame.toGeo(halfM, halfM);
  g.water = g.scene.primitives.add(new Cesium.Primitive({
    geometryInstances: new Cesium.GeometryInstance({
      geometry: new Cesium.RectangleGeometry({ rectangle: Cesium.Rectangle.fromDegrees(w, s, e, n), height: level, vertexFormat: Cesium.EllipsoidSurfaceAppearance.VERTEX_FORMAT }),
    }),
    appearance: new Cesium.EllipsoidSurfaceAppearance({
      aboveGround: true,
      material: new Cesium.Material({ fabric: { type: 'Water', uniforms: {
        baseWaterColor: new Cesium.Color(0.12, 0.32, 0.42, 0.78), blendColor: new Cesium.Color(0.2, 0.45, 0.5, 0.6),
        normalMap: Cesium.buildModuleUrl('Assets/Textures/waterNormals.jpg'), frequency: 1600, animationSpeed: 0.012, amplitude: 4, specularIntensity: 0.6,
      } } }),
    }),
  }));
}

// ── draped canvas overlays ───────────────────────────────────────────────────
// Static map graphics (zones, corridors, grid, borders) are painted into canvases and draped
// on the terrain as imagery layers: zero per-frame cost, unlike ground-clamped polylines.
const OVERLAY_PX = 4096;
export function aoRect(g) {
  const [w, s] = g.frame.toGeo(-g.halfM, -g.halfM), [e, n] = g.frame.toGeo(g.halfM, g.halfM);
  return { w, s, e, n };
}
/** draw(ctx, toPx) where toPx(x, y) maps local metres → canvas pixels. */
export async function overlay(g, name, draw, { rect = null, px = OVERLAY_PX, lonlat = false } = {}) {
  const r = rect || aoRect(g);
  const c = document.createElement('canvas');
  c.width = c.height = px;
  const ctx = c.getContext('2d');
  const toPx = lonlat
    ? (lon, lat) => [((lon - r.w) / (r.e - r.w)) * px, ((r.n - lat) / (r.n - r.s)) * px]
    : (x, y) => { const [lon, lat] = g.frame.toGeo(x, y); return [((lon - r.w) / (r.e - r.w)) * px, ((r.n - lat) / (r.n - r.s)) * px]; };
  ctx.lineCap = 'round'; ctx.lineJoin = 'round';
  draw(ctx, toPx, px / ((r.e - r.w) * g.frame.mx));
  const blob = await new Promise((res) => c.toBlob(res, 'image/png'));
  const url = URL.createObjectURL(blob);
  const provider = await Cesium.SingleTileImageryProvider.fromUrl(url, { rectangle: Cesium.Rectangle.fromDegrees(r.w, r.s, r.e, r.n) });
  const old = g.overlays[name];
  const layer = g.viewer.imageryLayers.addImageryProvider(provider);
  layer.show = old ? old.show : true;
  if (old) { g.viewer.imageryLayers.remove(old, true); }
  g.overlays[name] = layer;
  return layer;
}

function stroke(ctx, pts, toPx, { color, width = 2, dash = null }) {
  ctx.beginPath();
  pts.forEach((p, i) => { const [a, b] = toPx(p[0], p[1]); i ? ctx.lineTo(a, b) : ctx.moveTo(a, b); });
  ctx.strokeStyle = color; ctx.lineWidth = width; ctx.setLineDash(dash || []);
  ctx.stroke();
  ctx.setLineDash([]);
}
function text(ctx, str, x, y, { color = '#fff', size = 22, weight = 700, align = 'center' } = {}) {
  ctx.font = `${weight} ${size}px Rajdhani, sans-serif`;
  ctx.textAlign = align; ctx.textBaseline = 'middle';
  ctx.lineWidth = Math.max(3, size / 5); ctx.strokeStyle = 'rgba(0,0,0,.75)'; ctx.strokeText(str, x, y);
  ctx.fillStyle = color; ctx.fillText(str, x, y);
}

// One composite AO overlay (borders, AO frame, zones, corridors, coarse grid + numbers) plus a fine
// 1 km grid for close zoom and a coarse regional border layer for the strategic view. Every extra
// imagery layer is blended into every terrain tile, so keep the visible count at ≤ 2.
async function loadBorders(g) {
  const res = await fetch('/static/data/borders.geojson');
  g.borderData = await res.json();
  const paint = (ctx, toPx) => {
    for (const f of g.borderData.features) {
      const loc = f.properties.kind === 'loc';
      ctx.beginPath();
      f.geometry.coordinates.forEach((p, i) => { const [a, b] = toPx(p[0], p[1]); i ? ctx.lineTo(a, b) : ctx.moveTo(a, b); });
      ctx.strokeStyle = 'rgba(0,0,0,.45)'; ctx.lineWidth = 3.5; ctx.setLineDash([]); ctx.stroke();
      ctx.strokeStyle = loc ? '#ff8c42' : '#ffd166'; ctx.lineWidth = 2; ctx.setLineDash(loc ? [8, 6] : []); ctx.stroke();
    }
    ctx.setLineDash([]);
  };
  const layer = await overlay(g, 'borders_region', paint, { rect: { w: 64, s: 20, e: 84, n: 38 }, px: 4096, lonlat: true });
  layer.show = g.scene.camera.positionCartographic.height > 120000;
  redrawMap(g);
}

export function redrawMap(g) {
  clearTimeout(g._mapTimer);
  g._mapTimer = setTimeout(() => paintMap(g), 30);
}
function paintMap(g) {
  const st = g.mapState;
  const h = g.halfM;
  overlay(g, 'map', (ctx, toPx, pxPerM) => {
    const lw = (m, min) => Math.max(min, m * pxPerM);
    if (st.show.zones) for (const z of st.zones) {
      if (!z.polygon || z.polygon.length < 3) continue;
      const col = z.side === 'BLUE' ? '54,184,255' : '255,75,62';
      ctx.beginPath();
      z.polygon.forEach((p, i) => { const [a, b] = toPx(p[0], p[1]); i ? ctx.lineTo(a, b) : ctx.moveTo(a, b); });
      ctx.closePath();
      ctx.fillStyle = `rgba(${col},0.15)`; ctx.fill();
      ctx.strokeStyle = 'rgba(0,0,0,.5)'; ctx.lineWidth = 7; ctx.stroke();
      ctx.strokeStyle = `rgba(${col},0.95)`; ctx.lineWidth = 4.5; ctx.stroke();
      const cx = z.polygon.reduce((a, p) => a + p[0], 0) / z.polygon.length, cy = z.polygon.reduce((a, p) => a + p[1], 0) / z.polygon.length;
      const [tx, ty] = toPx(cx, cy);
      text(ctx, `${z.side} ZONE`, tx, ty, { color: `rgb(${col})`, size: 84 });
    }
    if (st.show.grid) {
      for (const step of [10000, 2000]) for (let v = -h; v <= h + 1; v += step) {
        const major = Math.round(v + h) % 10000 === 0;
        if (step === 2000 && major) continue;
        const col = major ? 'rgba(255,255,255,.5)' : 'rgba(255,255,255,.22)';
        stroke(ctx, [[v, -h], [v, h]], toPx, { color: col, width: major ? 2.5 : 1.2 });
        stroke(ctx, [[-h, v], [h, v]], toPx, { color: col, width: major ? 2.5 : 1.2 });
      }
      for (let v = -h; v <= h + 1; v += 2000) {
        const idx = String(Math.round((v + h) / 1000)).padStart(2, '0');
        const [x1, y1] = toPx(v, -h), [x2, y2] = toPx(-h, v);
        text(ctx, idx, x1, y1 - 18, { size: 20 });
        text(ctx, idx, x2 + 22, y2, { size: 20 });
        if (Math.round(v + h) % 10000 === 0) for (let w = -h + 10000; w < h; w += 10000) {
          const [gx, gy] = toPx(v, w);
          text(ctx, `${idx}${String(Math.round((w + h) / 1000)).padStart(2, '0')}`, gx + 34, gy - 14, { size: 18, weight: 600, color: 'rgba(255,255,255,.75)' });
        }
      }
    }
    if (st.show.corridors) st.corridors.forEach((c, i) => {
      stroke(ctx, c.path, toPx, { color: 'rgba(0,0,0,.4)', width: lw(c.width_m || 70, 6) + 3 });
      stroke(ctx, c.path, toPx, { color: 'rgba(255,224,102,.8)', width: lw(c.width_m || 70, 6), dash: [26, 16] });
      const p = c.path[Math.floor(c.path.length / 2)];
      const [tx, ty] = toPx(p[0], p[1]);
      text(ctx, `AVENUE ${i + 1}`, tx, ty - 34, { color: '#ffe066', size: 40 });
    });
    stroke(ctx, [[-h, -h], [h, -h], [h, h], [-h, h], [-h, -h]], toPx, { color: 'rgba(255,255,255,.65)', width: 5, dash: [26, 16] });
    if (st.show.borders && g.borderData) {
      const r = aoRect(g);
      const ll = (lon, lat) => [((lon - r.w) / (r.e - r.w)) * OVERLAY_PX, ((r.n - lat) / (r.n - r.s)) * OVERLAY_PX];
      for (const f of g.borderData.features) {
        const loc = f.properties.kind === 'loc';
        ctx.beginPath();
        f.geometry.coordinates.forEach((p, i) => { const [a, b] = ll(p[0], p[1]); i ? ctx.lineTo(a, b) : ctx.moveTo(a, b); });
        ctx.strokeStyle = 'rgba(0,0,0,.5)'; ctx.lineWidth = 8; ctx.setLineDash([]); ctx.stroke();
        ctx.strokeStyle = loc ? '#ff8c42' : '#ffd166'; ctx.lineWidth = 5; ctx.setLineDash(loc ? [22, 14] : []); ctx.stroke();
      }
      ctx.setLineDash([]);
    }
  });
}

/** Adaptive grid: the composite carries 10 km/2 km lines; a 1 km layer appears when zoomed in. */
function buildGrid(g) {
  const { halfM, scene } = g;
  overlay(g, 'grid_fine', (ctx, toPx) => {
    for (let v = -halfM; v <= halfM + 1; v += 1000) {
      if (Math.round(v + halfM) % 2000 === 0) continue;
      stroke(ctx, [[v, -halfM], [v, halfM]], toPx, { color: 'rgba(255,255,255,.18)', width: 1 });
      stroke(ctx, [[-halfM, v], [halfM, v]], toPx, { color: 'rgba(255,255,255,.18)', width: 1 });
    }
  }).then(() => update());
  const update = () => {
    const h = scene.camera.positionCartographic.height;
    const st = g.mapState;
    if (g.overlays.grid_fine) g.overlays.grid_fine.show = st.show.grid && h < 14000;
    if (g.overlays.borders_region) g.overlays.borders_region.show = st.show.borders && h > 120000;
  };
  scene.camera.changed.addEventListener(update);
  scene.camera.percentageChanged = 0.05;
  g._gridUpdate = update;
}

export function setLayer(g, name, on) {
  g.mapState.show[name] = on;
  redrawMap(g);
  g._gridUpdate && g._gridUpdate();
}

export function setImagery(g, key) {
  const layers = g.viewer.imageryLayers;
  const base = layers.get(0);
  const nl = layers.addImageryProvider(IMAGERY[key](), 0);
  if (base) layers.remove(base, true);
  return nl;
}

/** Terrain-precise pick → local [x, y, z] (or null). */
export function pickLocal(g, screen) {
  const scene = g.scene;
  let c = null;
  if (scene.pickPositionSupported) {
    c = scene.pickPosition(screen);
    if (c) {
      const carto = Cesium.Cartographic.fromCartesian(c);
      if (!Number.isFinite(carto.height) || carto.height < -1000) c = null;
    }
  }
  if (!c) {
    const ray = scene.camera.getPickRay(screen);
    c = ray && scene.globe.pick(ray, scene);
  }
  return c ? g.frame.fromCart(c) : null;
}

export function toggle2D(g) {
  const s = g.scene;
  if (s.mode === Cesium.SceneMode.SCENE3D) s.morphTo2D(0.8); else s.morphTo3D(0.8);
}

/** Search: grid reference "123 456", "lat, lon" or a place name (OpenStreetMap Nominatim). */
export async function search(g, q) {
  q = q.trim();
  let m = q.match(/^(\d{3})\s*(\d{3})$/);
  if (m) return flyLocal(g, +m[1] * 100 - g.halfM + 50, +m[2] * 100 - g.halfM + 50, 3000);
  m = q.match(/^(-?\d+(?:\.\d+)?)[,\s]+(-?\d+(?:\.\d+)?)$/);
  if (m) return flyGeo(g, +m[2], +m[1], 6000);
  const res = await fetch(`https://nominatim.openstreetmap.org/search?format=json&limit=1&q=${encodeURIComponent(q)}`);
  const js = await res.json();
  if (!js.length) throw new Error('place not found');
  return flyGeo(g, +js[0].lon, +js[0].lat, 9000);
}
export function flyLocal(g, x, y, h = 3000) { const [lon, lat] = g.frame.toGeo(x, y); return flyGeo(g, lon, lat, h); }
export function flyGeo(g, lon, lat, h) {
  g.viewer.camera.flyTo({ destination: Cesium.Cartesian3.fromDegrees(lon, lat - h / 160000, h), orientation: { heading: 0, pitch: -50 * DEG, roll: 0 }, duration: 1.2 });
}

/** Keep frame time near 60 fps: lower/raise resolution scale. */
function adaptiveQuality(g, lowPower) {
  const v = g.viewer;
  const maxScale = Math.min(window.devicePixelRatio || 1, lowPower ? 1 : 2);
  let frames = 0, t0 = performance.now();
  g.fps = 60;
  v.scene.postRender.addEventListener(() => {
    frames++;
    const t = performance.now();
    if (t - t0 < 2000) return;
    g.fps = (frames * 1000) / (t - t0);
    frames = 0; t0 = t;
    if (g.fps < 38 && v.resolutionScale > 0.6) v.resolutionScale = Math.max(0.6, v.resolutionScale - 0.15);
    else if (g.fps > 56 && v.resolutionScale < maxScale) v.resolutionScale = Math.min(maxScale, v.resolutionScale + 0.1);
  });
}

/** Zones (TR-2 / VZ-2): force-coloured fill, outline and label, draped on the terrain. */
export function drawZones(g, zones) { g.mapState.zones = zones; redrawMap(g); }
export function drawZone(g, side, poly) {
  g.mapState.zones = [...g.mapState.zones.filter((z) => z.side !== side), { side, polygon: poly }];
  redrawMap(g);
}
export function drawCorridors(g, corridors) { g.mapState.corridors = corridors || []; redrawMap(g); }
export function clearLayer(g, name) { if (name === 'zones') drawZones(g, []); if (name === 'corridors') drawCorridors(g, []); }

export { gridRef };
