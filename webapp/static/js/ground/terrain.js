// Ground-level terrain: high-detail patch around the camera + full-AO far mesh, PBR texture
// splatting (by ground type & slope, with anti-tiling), dirt roads along corridors, water.
import * as THREE from 'three';
import { Water } from 'three/addons/objects/Water.js';
import { makeHeightField } from './height.js';

const GT = ['ROAD', 'GRASS', 'SAND', 'MUD', 'SNOW', 'ICE', 'ROCK', 'SALT', 'MARSH', 'WATER'];
// four texture layers per biome (+ road and rock handled specially)
const BIOME = {
  DESERT: { layers: ['sand', 'gravel', 'rock', 'cracked'], map: { SAND: 0, ROCK: 1, GRASS: 0, MUD: 3, SALT: 3 }, rock: 2,
    tint: [[1.04, 0.95, 0.82], [1, 0.93, 0.85], [1.0, 0.9, 0.8], [1.05, 0.95, 0.85]], rough: [0.95, 0.9, 0.85, 0.95], tile: [5, 4, 7, 6] },
  MONSOON: { layers: ['grass', 'mud', 'marsh', 'rock'], map: { GRASS: 0, MUD: 1, MARSH: 2, ROCK: 3 }, rock: 3,
    tint: [[0.9, 1.0, 0.85], [0.85, 0.8, 0.75], [0.8, 0.85, 0.75], [1, 1, 1]], rough: [0.9, 0.55, 0.6, 0.8], tile: [4, 4, 5, 7] },
  MOUNTAIN: { layers: ['grass', 'rock', 'snow', 'mud'], map: { GRASS: 0, ROCK: 1, SNOW: 2, MUD: 3 }, rock: 1,
    tint: [[0.95, 1.0, 0.85], [0.95, 0.93, 0.9], [1, 1, 1.03], [0.9, 0.85, 0.8]], rough: [0.92, 0.85, 0.6, 0.7], tile: [4, 9, 6, 4] },
  ICE: { layers: ['snow', 'rock', 'snow', 'gravel'], map: { SNOW: 0, ROCK: 1, ICE: 2 }, rock: 1,
    tint: [[1, 1, 1.03], [0.85, 0.85, 0.9], [0.78, 0.9, 1.08], [0.9, 0.9, 0.95]], rough: [0.65, 0.85, 0.15, 0.9], tile: [6, 9, 11, 5] },
  MARSH: { layers: ['cracked', 'mud', 'marsh', 'sand'], map: { SALT: 0, MUD: 1, MARSH: 2, WATER: 1, SAND: 3 }, rock: 1,
    tint: [[1.5, 1.47, 1.4], [0.85, 0.8, 0.75], [0.8, 0.85, 0.75], [1.05, 1, 0.92]], rough: [0.8, 0.5, 0.6, 0.9], tile: [7, 4, 5, 5] },
};

const texLoader = new THREE.TextureLoader();
const texCache = new Map();
function tex(name, srgb, renderer) {
  const key = name + srgb;
  if (texCache.has(key)) return texCache.get(key);
  const t = texLoader.load(`/static/textures/${name}`);
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.colorSpace = srgb ? THREE.SRGBColorSpace : THREE.NoColorSpace;
  t.anisotropy = Math.min(8, renderer.capabilities.getMaxAnisotropy());
  texCache.set(key, t);
  return t;
}

export async function loadTerrainData(scenarioId, version) {
  const buf = await (await fetch(`/api/scenarios/${scenarioId}/terrain/heights.bin${version ? `?v=${version}` : ''}`)).arrayBuffer();
  const dv = new DataView(buf);
  const mlen = dv.getUint32(0, true);
  const meta = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 4, mlen)));
  const off = 4 + mlen + ((4 - ((4 + mlen) % 4)) % 4);
  const n = meta.cols * meta.rows;
  const elev = new Float32Array(buf.slice(off, off + n * 4));
  const ground = new Uint8Array(buf.slice(off + n * 4, off + n * 5));
  return { meta, elev, ground };
}

export class GroundTerrain {
  constructor(renderer, scene, data, { patchSize = 3200, patchRes = 5 } = {}) {
    this.renderer = renderer;
    this.scene = scene;
    this.meta = data.meta;
    this.field = makeHeightField(data.meta, data.elev);
    this.height = this.field.height;
    this.patchSize = patchSize;
    this.patchRes = patchRes;
    const m = data.meta;
    this.ext = { x0: m.x0, y0: m.y0, w: (m.cols - 1) * m.cell_m, h: (m.rows - 1) * m.cell_m };
    this.biome = BIOME[m.biome] || BIOME.DESERT;
    this.material = this.makeMaterial(data);
    this.buildFar(data);
    this.patch = null;
    this.patchId = 0;
    this.worker = new Worker(new URL('./terrain-worker.js', import.meta.url), { type: 'module' });
    this.worker.postMessage({ type: 'init', meta: m, elev: data.elev.buffer.slice(0) });
    this.worker.onmessage = (e) => this.onPatch(e.data);
    this.pending = false;
    this.center = null;
    if (m.water_level != null) this.addWater(m.water_level);
  }

  // ── material ──────────────────────────────────────────────────────────────
  makeMaterial(data) {
    const r = this.renderer, B = this.biome, m = data.meta;
    // splat weights: one RGBA texel per terrain cell (layer 0-3), softened with a 3×3 blur
    const W = m.cols, H = m.rows;
    const raw = new Float32Array(W * H * 4);
    for (let k = 0; k < W * H; k++) {
      const li = B.map[GT[data.ground[k]]] ?? 0;
      raw[k * 4 + li] = 1;
    }
    const px = new Uint8Array(W * H * 4);
    for (let j = 0; j < H; j++) for (let i = 0; i < W; i++) {
      for (let c = 0; c < 4; c++) {
        let s = 0, n = 0;
        for (let dj = -1; dj <= 1; dj++) for (let di = -1; di <= 1; di++) {
          const ii = Math.min(W - 1, Math.max(0, i + di)), jj = Math.min(H - 1, Math.max(0, j + dj));
          s += raw[(jj * W + ii) * 4 + c]; n++;
        }
        px[(j * W + i) * 4 + c] = Math.round((s / n) * 255); // DataTexture row 0 = v 0 = south
      }
    }
    const weights = new THREE.DataTexture(px, W, H, THREE.RGBAFormat);
    weights.magFilter = THREE.LinearFilter; weights.minFilter = THREE.LinearFilter; weights.needsUpdate = true;
    this.roadMask = this.makeRoadMask(m.corridors || []);
    const L = B.layers;
    const mat = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.9, metalness: 0 });
    mat.normalMap = tex(`${L[0]}_nor.jpg`, false, r); // enables tangent-space normal mapping (we override sampling)
    mat.normalScale = new THREE.Vector2(1, 1);
    const U = {
      uW: { value: weights }, uRoad: { value: this.roadMask },
      uExt: { value: new THREE.Vector4(this.ext.x0, this.ext.y0, this.ext.w, this.ext.h) },
      uTint: { value: B.tint.map((t) => new THREE.Vector3(...t)) }, uRough: { value: B.rough }, uTile: { value: B.tile },
      uRockLayer: { value: B.rock },
      uNear: { value: new THREE.Vector4(0, 0, -1, 0) }, // cx, cy, half (−1 = none), mode (1 = far mesh)
    };
    for (let i = 0; i < 4; i++) { U[`uD${i}`] = { value: tex(`${L[i]}_diff.jpg`, true, r) }; U[`uN${i}`] = { value: tex(`${L[i]}_nor.jpg`, false, r) }; }
    U.uDR = { value: tex('road_diff.jpg', true, r) }; U.uNR = { value: tex('road_nor.jpg', false, r) };
    mat.onBeforeCompile = (sh) => {
      Object.assign(sh.uniforms, U);
      sh.vertexShader = sh.vertexShader
        .replace('#include <common>', '#include <common>\nvarying vec2 vWXY;\nvarying vec3 vWN;\nvarying float vWY;\nuniform vec4 uNear;')
        .replace('#include <begin_vertex>', `#include <begin_vertex>
          vWXY = vec2(position.x, -position.z);
          vWN = normal;
          vWY = position.y;
          if (uNear.w > 0.5 && uNear.z > 0.0 && abs(vWXY.x - uNear.x) < uNear.z && abs(vWXY.y - uNear.y) < uNear.z) transformed.y -= 300.0;`);
      sh.fragmentShader = sh.fragmentShader
        .replace('#include <common>', `#include <common>
          varying vec2 vWXY; varying vec3 vWN; varying float vWY;
          uniform sampler2D uW, uRoad, uD0, uD1, uD2, uD3, uN0, uN1, uN2, uN3, uDR, uNR;
          uniform vec4 uExt; uniform vec3 uTint[4]; uniform float uRough[4]; uniform float uTile[4]; uniform int uRockLayer;
          float h21(vec2 p){ p = fract(p*vec2(123.34,456.21)); p += dot(p,p+45.32); return fract(p.x*p.y); }
          float vn(vec2 p){ vec2 i=floor(p), f=fract(p); f=f*f*(3.0-2.0*f);
            return mix(mix(h21(i),h21(i+vec2(1,0)),f.x), mix(h21(i+vec2(0,1)),h21(i+vec2(1,1)),f.x), f.y); }
          // two-scale sampling with noise blend breaks visible tiling
          vec4 tri(sampler2D t, vec2 p, float tile, float m){
            // three decorrelated samples (rotated / scaled) blended by noise → no visible repeat grid
            vec2 q = mat2(0.8, -0.6, 0.6, 0.8) * p;
            vec4 a = texture2D(t, p/tile), b = texture2D(t, q/(tile*1.73) + 0.31), c = texture2D(t, p/(tile*6.1) + 0.67);
            return mix(mix(a, b, smoothstep(0.3, 0.7, m)), c, 0.3);
          }
          // triplanar projection for cliffs: no stretching on steep faces
          vec4 triR(sampler2D t, float tile){
            vec3 b = pow(abs(vWN), vec3(4.0)); b /= (b.x+b.y+b.z);
            return texture2D(t, vec2(vWXY.y, vWY)/tile)*b.x + texture2D(t, vWXY/tile)*b.y + texture2D(t, vec2(vWXY.x, vWY)/tile)*b.z;
          }
          vec4 splatW(){
            vec4 w = texture2D(uW, vec2((vWXY.x-uExt.x)/uExt.z, (vWXY.y-uExt.y)/uExt.w));
            float n = vn(vWXY/23.0)*0.6 + vn(vWXY/5.0)*0.4;
            w = max(w + (n-0.5)*0.35, 0.0);
            float steep = smoothstep(0.86, 0.68, vWN.y);
            vec4 rock = vec4(0.0); rock[uRockLayer] = 1.0;
            w = mix(w, rock, steep);
            return w / max(1e-3, w.x+w.y+w.z+w.w);
          }`)
        .replace('#include <map_fragment>', `
          vec4 sw = splatW();
          float mv = vn(vWXY/17.0)*0.7 + vn(vWXY/53.0)*0.3;
          vec3 c0 = tri(uD0, vWXY, uTile[0], mv).rgb*uTint[0], c1 = tri(uD1, vWXY, uTile[1], mv).rgb*uTint[1];
          vec3 c2 = tri(uD2, vWXY, uTile[2], mv).rgb*uTint[2], c3 = tri(uD3, vWXY, uTile[3], mv).rgb*uTint[3];
          float cliff = smoothstep(0.92, 0.6, vWN.y);
          c${B.rock} = mix(c${B.rock}, triR(uD${B.rock}, uTile[${B.rock}]).rgb*uTint[${B.rock}], cliff);
          vec3 sc = c0*sw.x + c1*sw.y + c2*sw.z + c3*sw.w;
          float road = texture2D(uRoad, vec2((vWXY.x-uExt.x)/uExt.z, (vWXY.y-uExt.y)/uExt.w)).r; // canvas flipY: top row (north) = v 1
          road = smoothstep(0.25, 0.75, road + (vn(vWXY/3.0)-0.5)*0.4);
          sc = mix(sc, tri(uDR, vWXY, 5.0, mv).rgb*vec3(0.95,0.9,0.85), road);
          sc *= 0.85 + 0.3*vn(vWXY/180.0);
          diffuseColor.rgb *= sc;`)
        .replace('#include <roughnessmap_fragment>', `float roughnessFactor = dot(sw, vec4(uRough[0],uRough[1],uRough[2],uRough[3]))*(1.0-road) + 0.95*road;`)
        .replace('#include <normal_fragment_maps>', `
          vec3 nr${B.rock} = mix(tri(uN${B.rock}, vWXY, uTile[${B.rock}], mv).xyz, vec3(0.5, 0.5, 1.0), cliff*0.6);
          vec3 mn = (${[0,1,2,3].map((i) => i === B.rock ? `nr${i}*sw[${i}]` : `tri(uN${i}, vWXY, uTile[${i}], mv).xyz*sw[${i}]`).join(' + ')});
          mn = mix(mn, tri(uNR, vWXY, 5.0, mv).xyz, road) * 2.0 - 1.0;
          mn.xy *= normalScale * 1.3;
          normal = normalize( tbn * mn );`);
      this.shader = sh;
    };
    this.uniforms = U;
    return mat;
  }

  makeRoadMask(corridors) {
    const S = 4096, c = document.createElement('canvas');
    c.width = c.height = S;
    const g = c.getContext('2d');
    g.fillStyle = '#000'; g.fillRect(0, 0, S, S);
    const toPx = (x, y) => [((x - this.ext.x0) / this.ext.w) * S, (1 - (y - this.ext.y0) / this.ext.h) * S];
    const mpp = this.ext.w / S;
    g.lineCap = 'round'; g.lineJoin = 'round';
    for (const cor of corridors) {
      g.beginPath();
      cor.path.forEach((p, i) => { const [a, b] = toPx(p[0], p[1]); i ? g.lineTo(a, b) : g.moveTo(a, b); });
      g.strokeStyle = '#fff'; g.lineWidth = (cor.width || 11) / mpp; g.filter = `blur(${(cor.width ? 1 : 3) / mpp}px)`; g.stroke();
    }
    const t = new THREE.CanvasTexture(c);
    t.colorSpace = THREE.NoColorSpace;
    return t;
  }

  // ── meshes ────────────────────────────────────────────────────────────────
  buildFar(data) {
    const { x0, y0, w, h } = this.ext;
    const n = 361;
    const geo = new THREE.PlaneGeometry(w, h, n - 1, n - 1);
    geo.rotateX(-Math.PI / 2);
    const pos = geo.attributes.position;
    for (let k = 0; k < pos.count; k++) {
      const x = pos.getX(k) + x0 + w / 2, z = pos.getZ(k) - (y0 + h / 2);
      pos.setXYZ(k, x, this.field.grid(x, -z) - 0.3, z);
    }
    geo.computeVertexNormals();
    const uv = geo.attributes.uv;
    for (let k = 0; k < uv.count; k++) uv.setXY(k, pos.getX(k), -pos.getZ(k));
    geo.computeBoundingSphere();
    const farMat = this.material.clone();
    farMat.onBeforeCompile = this.material.onBeforeCompile;
    farMat.customProgramCacheKey = () => 'far';
    this.farUniformsNear = new THREE.Vector4(0, 0, -1, 1);
    farMat.userData.isFar = true;
    const prev = farMat.onBeforeCompile;
    farMat.onBeforeCompile = (sh) => { prev(sh); sh.uniforms.uNear = { value: this.farUniformsNear }; };
    this.far = new THREE.Mesh(geo, farMat);
    this.far.receiveShadow = true;
    this.scene.add(this.far);
    // skirt ring beyond the AO so the horizon isn't an abrupt edge
    const skirt = new THREE.Mesh(new THREE.RingGeometry(Math.max(w, h) * 0.72, Math.max(w, h) * 4, 64, 1),
      new THREE.MeshStandardMaterial({ color: 0x9a8f78, roughness: 1 }));
    skirt.rotation.x = -Math.PI / 2;
    let minE = Infinity;
    for (let k = 0; k < data.elev.length; k += 7) if (data.elev[k] < minE) minE = data.elev[k];
    skirt.position.set(x0 + w / 2, minE - 40, -(y0 + h / 2)); // below the lowest terrain, never a ceiling
    this.skirt = skirt;
    this.scene.add(skirt);
  }

  /** Recenter the detail patch when the focus point moves far enough. */
  update(focusX, focusY) {
    const step = this.patchSize * 0.2;
    if (this.pending) return;
    if (this.center && Math.abs(focusX - this.center[0]) < step && Math.abs(focusY - this.center[1]) < step) return;
    const cx = Math.round(focusX / this.patchRes) * this.patchRes, cy = Math.round(focusY / this.patchRes) * this.patchRes;
    this.pending = true;
    this.worker.postMessage({ type: 'patch', cx, cy, size: this.patchSize, res: this.patchRes, id: ++this.patchId });
  }

  onPatch(d) {
    this.pending = false;
    const n = d.n;
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(d.pos, 3));
    geo.setAttribute('normal', new THREE.BufferAttribute(d.nor, 3));
    const uv = new Float32Array(n * n * 2);
    for (let k = 0; k < n * n; k++) { uv[k * 2] = d.pos[k * 3]; uv[k * 2 + 1] = -d.pos[k * 3 + 2]; }
    geo.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
    const idx = new Uint32Array((n - 1) * (n - 1) * 6);
    let t = 0;
    for (let j = 0; j < n - 1; j++) for (let i = 0; i < n - 1; i++) {
      const a = j * n + i, b = a + 1, c = a + n, e = c + 1;
      idx[t++] = a; idx[t++] = b; idx[t++] = c; idx[t++] = b; idx[t++] = e; idx[t++] = c;
    }
    geo.setIndex(new THREE.BufferAttribute(idx, 1));
    geo.computeBoundingSphere();
    const mesh = new THREE.Mesh(geo, this.material);
    mesh.receiveShadow = true;
    if (this.patch) { this.scene.remove(this.patch); this.patch.geometry.dispose(); }
    this.patch = mesh;
    this.scene.add(mesh);
    this.center = [d.cx, d.cy];
    this.farUniformsNear.set(d.cx, d.cy, d.size / 2 - this.patchRes * 3, 1);
    this.onRecenter && this.onRecenter(d.cx, d.cy);
  }

  addWater(level) {
    const { x0, y0, w, h } = this.ext;
    const normals = new THREE.TextureLoader().load('/static/textures/waternormals.jpg', (t) => { t.wrapS = t.wrapT = THREE.RepeatWrapping; });
    this.water = new Water(new THREE.PlaneGeometry(w, h, 1, 1), {
      textureWidth: 1024, textureHeight: 1024, waterNormals: normals, sunDirection: new THREE.Vector3(0.5, 0.7, 0.2),
      sunColor: 0xffffff, waterColor: 0x1d4b52, distortionScale: 2.2, fog: true,
    });
    this.water.rotation.x = -Math.PI / 2;
    this.water.position.set(x0 + w / 2, level, -(y0 + h / 2));
    this.scene.add(this.water);
  }

  /** Ray march against the height field. Returns local [x, y, z] or null. */
  raycast(origin, dir, maxDist = 12000) {
    let t = 0, step = 2;
    let prevAbove = true;
    while (t < maxDist) {
      const x = origin.x + dir.x * t, y = -(origin.z + dir.z * t), z = origin.y + dir.y * t;
      const h = this.height(x, y);
      if (z <= h) {
        // refine
        let lo = t - step, hi = t;
        for (let i = 0; i < 8; i++) {
          const mid = (lo + hi) / 2;
          const mx = origin.x + dir.x * mid, my = -(origin.z + dir.z * mid), mz = origin.y + dir.y * mid;
          if (mz <= this.height(mx, my)) hi = mid; else lo = mid;
        }
        const fx = origin.x + dir.x * hi, fy = -(origin.z + dir.z * hi);
        return [fx, fy, this.height(fx, fy)];
      }
      prevAbove = true;
      step = Math.min(40, 2 + t * 0.02);
      t += step;
    }
    return null;
  }
}
