"""Real elevation ingest from AWS Terrain Tiles (Mapzen terrarium encoding, open data).

height = (R·256 + G + B/256) − 32768 m. Tiles are cached on disk. If anything fails the caller
falls back to flat, unobstructed going (TR-5).
"""
from __future__ import annotations

import io
import math
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from .geo import to_geo
from .terrain import TerrainGrid
from .theatres import Theatre

TERRARIUM_URL = os.environ.get('TERRARIUM_URL', 'https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png')


def choose_zoom(lat: float, cell_m: float) -> int:
    for z in range(10, 15):
        if 156543.03 * math.cos(math.radians(lat)) / 2 ** z <= cell_m * 0.8:
            return z
    return 14


def _lon2px(lon, z):
    return (lon + 180) / 360 * 256 * 2 ** z


def _lat2px(lat, z):
    s = np.sin(np.radians(lat))
    return (0.5 - np.log((1 + s) / (1 - s)) / (4 * np.pi)) * 256 * 2 ** z


def _fetch(url: str, attempts: int = 4) -> bytes:
    last = None
    for a in range(attempts):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'ai-dssw/1.0'})
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.read()
        except Exception as e:  # network blips: retry with backoff
            last = e
            time.sleep(0.4 * 2 ** a)
    raise RuntimeError(f'tile download failed: {url}: {last}')


def _load_tile(cache_dir: Path, z: int, x: int, y: int, offline: bool) -> np.ndarray:
    from PIL import Image

    f = cache_dir / str(z) / str(x) / f'{y}.png'
    if f.exists():
        data = f.read_bytes()
    else:
        if offline:
            raise RuntimeError('offline terrain mode and tile not cached')
        data = _fetch(TERRARIUM_URL.format(z=z, x=x, y=y))
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
    rgb = np.asarray(Image.open(io.BytesIO(data)).convert('RGB'), dtype=np.float32)
    return rgb[..., 0] * 256 + rgb[..., 1] + rgb[..., 2] / 256 - 32768


def grid_meta(lat: float, lon: float, half_km: float, cell_m: float, theatre: Theatre, source: str) -> dict:
    half = half_km * 1000
    n = int(round(2 * half / cell_m)) + 1
    return dict(origin_lat=lat, origin_lon=lon, cell_m=cell_m, cols=n, rows=n, x0=-half, y0=-half,
                water_level=theatre.water_level if source != 'flat' else None, biome=theatre.biome, source=source)


def ingest_terrarium(lat: float, lon: float, half_km: float, cell_m: float, theatre: Theatre,
                     cache_dir: Path, offline: bool = False) -> TerrainGrid:
    meta = grid_meta(lat, lon, half_km, cell_m, theatre, 'terrarium')
    z = choose_zoom(lat, cell_m)
    meta['zoom'] = z
    half = half_km * 1000
    origin = (lat, lon)
    sw_lon, sw_lat = to_geo(origin, -half, -half)
    ne_lon, ne_lat = to_geo(origin, half, half)
    tx0, tx1 = int(_lon2px(sw_lon, z) // 256), int(_lon2px(ne_lon, z) // 256)
    ty0, ty1 = int(_lat2px(ne_lat, z) // 256), int(_lat2px(sw_lat, z) // 256)
    coords = [(x, y) for x in range(tx0, tx1 + 1) for y in range(ty0, ty1 + 1)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        tiles = dict(zip(coords, pool.map(lambda c: _load_tile(cache_dir, z, c[0], c[1], offline), coords)))

    # Mosaic the tiles, then bilinear-sample every grid cell.
    W, H = (tx1 - tx0 + 1) * 256, (ty1 - ty0 + 1) * 256
    mosaic = np.zeros((H, W), dtype=np.float32)
    for (x, y), t in tiles.items():
        mosaic[(y - ty0) * 256:(y - ty0 + 1) * 256, (x - tx0) * 256:(x - tx0 + 1) * 256] = t
    n = meta['cols']
    jj, ii = np.mgrid[0:n, 0:n]
    xs = -half + ii * cell_m
    ys = -half + jj * cell_m
    from .geo import m_per_deg_lat, m_per_deg_lon
    lons = lon + xs / m_per_deg_lon(lat)
    lats = lat + ys / m_per_deg_lat(lat)
    px = _lon2px(lons, z) - tx0 * 256 - 0.5
    py = _lat2px(lats, z) - ty0 * 256 - 0.5
    px = np.clip(px, 0, W - 1.001)
    py = np.clip(py, 0, H - 1.001)
    i0, j0 = px.astype(np.int32), py.astype(np.int32)
    tx, ty = px - i0, py - j0
    a, b = mosaic[j0, i0], mosaic[j0, i0 + 1]
    c, d = mosaic[j0 + 1, i0], mosaic[j0 + 1, i0 + 1]
    elev = a * (1 - tx) * (1 - ty) + b * tx * (1 - ty) + c * (1 - tx) * ty + d * tx * ty
    elev = np.where(np.isfinite(elev) & (elev > -500), elev, 0).astype(np.float32)
    return TerrainGrid(meta, elev)


def flat_terrain(lat: float, lon: float, half_km: float, cell_m: float, theatre: Theatre) -> TerrainGrid:
    return TerrainGrid.flat_grid(grid_meta(lat, lon, half_km, cell_m, theatre, 'flat'), theatre.flat_elevation)
