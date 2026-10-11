"""Fine terrain grid: elevation, slope, class, ground, mobility, concealment, LOS (TR-3..7)."""
from __future__ import annotations

import io
import math
import zlib

import numpy as np

from .geo import DEG, EARTH_R, REFRACTION_K, clamp, curvature_drop
from .physics import GROUND
from .theatres import GROUND_TYPES, G_IDX, classify_ground, concealment

CLASS_NAMES = ['FLAT', 'SLOPED', 'STEEP']
_GROUND_SPEED = np.array([GROUND[g]['speed'] for g in GROUND_TYPES], dtype=np.float32)


class TerrainGrid:
    """Regular grid on the local plane. Cell (i, j) centre = (x0 + i·cell, y0 + j·cell); row j = north."""

    def __init__(self, meta: dict, elev: np.ndarray):
        self.meta = meta
        self.cell = float(meta['cell_m'])
        self.cols = int(meta['cols'])
        self.rows = int(meta['rows'])
        self.x0 = float(meta['x0'])
        self.y0 = float(meta['y0'])
        self.water_level = meta.get('water_level')
        self.flat = meta.get('source') == 'flat'
        self.elev = np.asarray(elev, dtype=np.float32).reshape(self.rows, self.cols)
        self.corridor_dist = np.full((self.rows, self.cols), 1e9, dtype=np.float32)
        self.corridors: list = []
        self._derive()

    # ── construction ────────────────────────────────────────────────────────
    @classmethod
    def flat_grid(cls, meta: dict, elevation: float) -> 'TerrainGrid':
        meta = dict(meta, source='flat')
        return cls(meta, np.full((meta['rows'], meta['cols']), elevation, dtype=np.float32))

    def _derive(self):
        E = self.elev
        c = self.cell
        gy, gx = np.gradient(E, c, c)
        self.slope = (np.degrees(np.arctan(np.hypot(gx, gy)))).astype(np.float32)
        self.cls = np.where(self.slope < 6, 0, np.where(self.slope < 18, 1, 2)).astype(np.uint8)
        # Relief: elevation minus the ~600 m local mean (box filter via an integral image).
        r = max(1, round(300 / c))
        pad = np.pad(E.astype(np.float64), r, mode='edge')
        ii = np.pad(pad.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
        k = 2 * r + 1
        box = (ii[k:, k:] - ii[:-k, k:] - ii[k:, :-k] + ii[:-k, :-k]) / (k * k)
        relief = (E - box).astype(np.float32)
        jj, iiw = np.mgrid[0:self.rows, 0:self.cols]
        X = self.x0 + iiw * c
        Y = self.y0 + jj * c
        wl = self.water_level
        wd = (wl - E) if wl is not None else np.full(E.shape, -99.0, dtype=np.float32)
        biome = self.meta['biome']
        self.ground = classify_ground(biome, E, self.slope, wd, X, Y, relief)
        self.conceal = concealment(biome, self.ground, self.slope, relief, X, Y)
        mob = _GROUND_SPEED[self.ground] * (1 - self.slope / 32)
        mob[self.ground == G_IDX['WATER']] = 0.05
        self.mobility = np.clip(mob, 0, 1).astype(np.float32)
        if self.meta.get('village'):
            from .village import apply_ground
            apply_ground(self)

    def set_corridors(self, paths: list, width_m: float):
        """Distance-to-corridor field; cells within ~2× width count as ROAD."""
        self.corridors = paths
        self.corridor_dist.fill(1e9)
        reach = width_m * 4
        c = self.cell
        for path in paths:
            for (ax, ay), (bx, by) in zip(path[:-1], path[1:]):
                i0 = max(0, int((min(ax, bx) - reach - self.x0) // c))
                i1 = min(self.cols - 1, int((max(ax, bx) + reach - self.x0) // c) + 1)
                j0 = max(0, int((min(ay, by) - reach - self.y0) // c))
                j1 = min(self.rows - 1, int((max(ay, by) + reach - self.y0) // c) + 1)
                if i1 < i0 or j1 < j0:
                    continue
                jj, ii = np.mgrid[j0:j1 + 1, i0:i1 + 1]
                px = self.x0 + ii * c
                py = self.y0 + jj * c
                dx, dy = bx - ax, by - ay
                l2 = dx * dx + dy * dy or 1.0
                t = np.clip(((px - ax) * dx + (py - ay) * dy) / l2, 0, 1)
                d = np.hypot(px - (ax + t * dx), py - (ay + t * dy)).astype(np.float32)
                sub = self.corridor_dist[j0:j1 + 1, i0:i1 + 1]
                np.minimum(sub, d, out=sub)
        self.corridor_width = width_m

    # ── point queries ──────────────────────────────────────────────────────
    @property
    def half_m(self) -> float:
        return (self.cols - 1) * self.cell / 2

    def in_bounds(self, x: float, y: float) -> bool:
        return (self.x0 <= x <= self.x0 + (self.cols - 1) * self.cell
                and self.y0 <= y <= self.y0 + (self.rows - 1) * self.cell)

    def clamp_xy(self, x: float, y: float) -> tuple[float, float]:
        return (clamp(x, self.x0, self.x0 + (self.cols - 1) * self.cell),
                clamp(y, self.y0, self.y0 + (self.rows - 1) * self.cell))

    def idx(self, x: float, y: float) -> tuple[int, int]:
        i = int(clamp(round((x - self.x0) / self.cell), 0, self.cols - 1))
        j = int(clamp(round((y - self.y0) / self.cell), 0, self.rows - 1))
        return j, i

    def height(self, x: float, y: float) -> float:
        fx = clamp((x - self.x0) / self.cell, 0, self.cols - 1.0001)
        fy = clamp((y - self.y0) / self.cell, 0, self.rows - 1.0001)
        i, j = int(fx), int(fy)
        tx, ty = fx - i, fy - j
        E = self.elev
        a, b = float(E[j, i]), float(E[j, i + 1])
        c, d = float(E[j + 1, i]), float(E[j + 1, i + 1])
        return a + (b - a) * tx + (c - a) * ty + (a - b - c + d) * tx * ty

    def heights(self, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
        fx = np.clip((xs - self.x0) / self.cell, 0, self.cols - 1.0001)
        fy = np.clip((ys - self.y0) / self.cell, 0, self.rows - 1.0001)
        i = fx.astype(np.int32)
        j = fy.astype(np.int32)
        tx, ty = fx - i, fy - j
        E = self.elev
        a, b, c, d = E[j, i], E[j, i + 1], E[j + 1, i], E[j + 1, i + 1]
        return a + (b - a) * tx + (c - a) * ty + (a - b - c + d) * tx * ty

    def surface(self, x: float, y: float) -> float:
        h = self.height(x, y)
        wl = self.water_level
        return wl if wl is not None and wl > h else h

    def gradient(self, x: float, y: float) -> tuple[float, float]:
        d = self.cell * 0.75
        return ((self.height(x + d, y) - self.height(x - d, y)) / (2 * d),
                (self.height(x, y + d) - self.height(x, y - d)) / (2 * d))

    def grade_along(self, x: float, y: float, heading: float) -> float:
        """Grade in percent along a compass heading (+ uphill)."""
        gx, gy = self.gradient(x, y)
        return (gx * math.sin(heading * DEG) + gy * math.cos(heading * DEG)) * 100

    def attitude(self, x: float, y: float, heading: float) -> tuple[float, float]:
        """(pitch + nose up, roll + right side down) for a vehicle on the slope."""
        gx, gy = self.gradient(x, y)
        h = heading * DEG
        along = gx * math.sin(h) + gy * math.cos(h)
        across = gx * math.cos(h) - gy * math.sin(h)
        return math.degrees(math.atan(along)), -math.degrees(math.atan(across))

    def slope_at(self, x, y) -> float:
        return float(self.slope[self.idx(x, y)])

    def ground_at(self, x, y) -> str:
        return GROUND_TYPES[int(self.ground[self.idx(x, y)])]

    def conceal_at(self, x, y) -> float:
        return float(self.conceal[self.idx(x, y)])

    def mobility_at(self, x, y) -> float:
        return float(self.mobility[self.idx(x, y)])

    def class_at(self, x, y) -> str:
        return CLASS_NAMES[int(self.cls[self.idx(x, y)])]

    def corridor_dist_at(self, x, y) -> float:
        return float(self.corridor_dist[self.idx(x, y)])

    def on_corridor(self, x, y) -> bool:
        return self.corridor_dist_at(x, y) < getattr(self, 'corridor_width', 70) * 2

    def water_depth(self, x, y) -> float:
        wl = self.water_level
        return 0.0 if wl is None else max(0.0, wl - self.height(x, y))

    # ── line of sight ──────────────────────────────────────────────────────
    def los(self, ax, ay, az, bx, by, bz) -> bool:
        """LOS over terrain with earth bulge d1·d2/(2kR), sampled at grid resolution."""
        if self.flat:
            return True
        d = math.hypot(bx - ax, by - ay)
        if d < self.cell:
            return True
        n = int(math.ceil(d / self.cell))
        t = np.arange(1, n, dtype=np.float64) / n
        xs = ax + (bx - ax) * t
        ys = ay + (by - ay) * t
        d1 = d * t
        bulge = d1 * (d - d1) / (2 * EARTH_R * REFRACTION_K)
        line = az + (bz - az) * t
        return bool(np.all(self.heights(xs, ys) + bulge <= line))

    def raycast(self, ox, oy, oz, dx, dy, dz, max_dist) -> tuple | None:
        """First terrain hit of a ray (aim point / rangefinder)."""
        ln = math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0
        dx, dy, dz = dx / ln, dy / ln, dz / ln
        step = self.cell / 2
        t = np.arange(step, max_dist, step)
        xs, ys, zs = ox + dx * t, oy + dy * t, oz + dz * t
        inside = (xs >= self.x0) & (ys >= self.y0) & (xs <= self.x0 + (self.cols - 1) * self.cell) & (ys <= self.y0 + (self.rows - 1) * self.cell)
        h = self.heights(xs, ys) - t * t / (2 * EARTH_R * REFRACTION_K)
        hit = np.nonzero((zs <= h) & inside)[0]
        if not len(hit):
            return None
        k = int(hit[0])
        return float(xs[k]), float(ys[k]), float(self.height(xs[k], ys[k]))

    # ── stats & serialisation ──────────────────────────────────────────────
    def stats(self) -> dict:
        n = self.elev.size
        counts = np.bincount(self.ground.ravel(), minlength=len(GROUND_TYPES))
        cls = np.bincount(self.cls.ravel(), minlength=3)
        return dict(
            min_elev=round(float(self.elev.min())), max_elev=round(float(self.elev.max())),
            mean_elev=round(float(self.elev.mean())), mean_slope=round(float(self.slope.mean()), 1),
            pct_flat=round(cls[0] / n * 100, 1), pct_sloped=round(cls[1] / n * 100, 1), pct_steep=round(cls[2] / n * 100, 1),
            pct_water=round(counts[G_IDX['WATER']] / n * 100, 1),
            ground={GROUND_TYPES[i]: round(c / n * 100, 1) for i, c in enumerate(counts) if c},
        )

    def elev_bytes(self) -> bytes:
        return zlib.compress(self.elev.astype(np.float32).tobytes(), 6)

    @classmethod
    def from_bytes(cls, meta: dict, blob: bytes) -> 'TerrainGrid':
        arr = np.frombuffer(zlib.decompress(blob), dtype=np.float32).copy()
        return cls(meta, arr.reshape(meta['rows'], meta['cols']))

    def layer_png(self, layer: str = 'relief', max_px: int = 512) -> bytes:
        """Small PNG of a terrain layer for the minimap / admin terrain tab."""
        from PIL import Image  # optional dependency only needed for previews

        step = max(1, self.cols // max_px)
        if layer == 'ground':
            pal = np.array([[150, 150, 150], [110, 150, 70], [222, 196, 140], [110, 85, 60], [240, 244, 250],
                            [190, 225, 245], [130, 120, 110], [235, 232, 220], [90, 120, 90], [40, 90, 160]], dtype=np.uint8)
            rgb = pal[self.ground[::step, ::step]]
        elif layer == 'slope':
            s = np.clip(self.slope[::step, ::step] / 40, 0, 1)
            rgb = np.stack([s * 255, (1 - s) * 200, 60 + 0 * s], -1).astype(np.uint8)
        else:
            E = self.elev[::step, ::step]
            gy, gx = np.gradient(E)
            shade = np.clip(0.6 + (gx - gy) * 0.02, 0, 1)
            en = (E - E.min()) / max(1.0, float(E.max() - E.min()))
            base = np.stack([80 + en * 120, 100 + en * 100, 70 + en * 90], -1)
            rgb = np.clip(base * shade[..., None] * 1.2, 0, 255).astype(np.uint8)
            if self.water_level is not None:
                rgb[E < self.water_level] = [40, 90, 160]
        img = Image.fromarray(rgb[::-1])  # row 0 = south → flip to north-up image
        buf = io.BytesIO()
        img.save(buf, 'PNG')
        return buf.getvalue()
