"""A* route planning on a coarse navigation grid, and mobility-corridor generation (TR-6)."""
from __future__ import annotations

import heapq
import math

import numpy as np

from .geo import dist_to_polyline, polygon_centroid
from .theatres import G_IDX

PROFILE_GRADE = {'TRACKED': 30, 'WHEELED': 25, 'AMPHIB': 28, 'HOVER': 21, 'AIR': 90}
CORRIDOR_WIDTH_M = 70


def nav_profile(spec: dict) -> str:
    if spec.get('air'):
        return 'AIR'
    if spec.get('hover'):
        return 'HOVER'
    if spec.get('swim_kph', 0) > 0:
        return 'AMPHIB'
    if spec['cls'] in ('APC', 'MLRS'):
        return 'WHEELED'
    return 'TRACKED'


class NavGrid:
    """Blocks of factor×factor terrain cells; cost per block and profile, Infinity = impassable."""

    def __init__(self, terrain, factor: int = 4):
        self.t = terrain
        self.factor = factor
        self.size = terrain.cell * factor
        R, C = terrain.rows, terrain.cols
        self.rows = math.ceil(R / factor)
        self.cols = math.ceil(C / factor)
        pr, pc = self.rows * factor - R, self.cols * factor - C

        def blocks(a):
            return np.pad(a, ((0, pr), (0, pc)), mode='edge').reshape(self.rows, factor, self.cols, factor)

        sl = blocks(terrain.slope)
        self.slope = 0.6 * sl.mean(axis=(1, 3)) + 0.4 * sl.max(axis=(1, 3))
        self.mob = blocks(terrain.mobility).mean(axis=(1, 3))
        self.water = blocks((terrain.ground == G_IDX['WATER']).astype(np.float32)).mean(axis=(1, 3))
        road = blocks((terrain.corridor_dist < terrain.cell * 2).astype(np.float32)).mean(axis=(1, 3))
        self.road = road > 0.15
        self.x0 = terrain.x0 + terrain.cell * (factor - 1) / 2
        self.y0 = terrain.y0 + terrain.cell * (factor - 1) / 2
        self._cost_cache: dict = {}

    def cost_field(self, profile: str, max_grade: float | None = None) -> np.ndarray:
        key = (profile, max_grade)
        if key in self._cost_cache:
            return self._cost_cache[key]
        if profile == 'AIR':
            c = np.ones((self.rows, self.cols))
        else:
            g = max_grade or PROFILE_GRADE[profile]
            c = 1 / np.maximum(0.08, self.mob)
            wet = self.water > 0.4
            if profile in ('TRACKED', 'WHEELED'):
                c = np.where(wet, np.inf, c)
            else:
                c = np.where(wet, 1.1 if profile == 'HOVER' else 4.5, c)
            c = c * (1 + self.slope / g * 1.5)
            c = np.where(self.road, c * (0.35 if profile == 'WHEELED' else 0.5), c)
            # Corridors are engineered roads (switchbacks): passable regardless of raw slope.
            c = np.where((self.slope > g) & ~self.road, np.inf, c)
        self._cost_cache[key] = c
        return c

    def to_cell(self, x, y) -> tuple[int, int]:
        i = int(min(self.cols - 1, max(0, round((x - self.x0) / self.size))))
        j = int(min(self.rows - 1, max(0, round((y - self.y0) / self.size))))
        return i, j

    def to_world(self, i, j) -> list:
        return [self.x0 + i * self.size, self.y0 + j * self.size]

    def passable(self, x, y, profile, max_grade=None) -> bool:
        i, j = self.to_cell(x, y)
        return bool(np.isfinite(self.cost_field(profile, max_grade)[j, i]))

    def nearest_passable(self, x, y, profile, max_grade=None) -> list | None:
        cost = self.cost_field(profile, max_grade)
        ti, tj = self.to_cell(x, y)
        if np.isfinite(cost[tj, ti]):
            return [x, y]
        for r in range(1, 40):
            for dj in range(-r, r + 1):
                for di in range(-r, r + 1):
                    if max(abs(di), abs(dj)) != r:
                        continue
                    i, j = ti + di, tj + dj
                    if 0 <= i < self.cols and 0 <= j < self.rows and np.isfinite(cost[j, i]):
                        return self.to_world(i, j)
        return None

    def find_path(self, start, goal, profile: str, max_grade=None, penalty=None, max_nodes=60000) -> list | None:
        """A* (8-neighbour, octile heuristic) → smoothed world polyline, or None."""
        if profile == 'AIR':
            return [list(start), list(goal)]
        cost = self.cost_field(profile, max_grade)
        if penalty is not None:
            cost = cost + penalty
        si, sj = self.to_cell(*start)
        g = self.nearest_passable(goal[0], goal[1], profile, max_grade)
        if g is None:
            return None
        exact_goal = g == [goal[0], goal[1]]
        ti, tj = self.to_cell(*g)
        cols, rows = self.cols, self.rows
        cost_l = cost.tolist()
        INF = math.inf
        best = {(si, sj): 0.0}
        came = {}
        h0 = 0.35
        heap = [(0.0, si, sj)]
        closed = set()
        nb = ((1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
              (1, 1, 1.4142), (1, -1, 1.4142), (-1, 1, 1.4142), (-1, -1, 1.4142))
        found = False
        while heap:
            _, i, j = heapq.heappop(heap)
            if (i, j) in closed:
                continue
            if i == ti and j == tj:
                found = True
                break
            closed.add((i, j))
            if len(closed) > max_nodes:
                return None
            gij = best[(i, j)]
            for di, dj, dl in nb:
                ni, nj = i + di, j + dj
                if not (0 <= ni < cols and 0 <= nj < rows) or (ni, nj) in closed:
                    continue
                c = cost_l[nj][ni]
                if c == INF:
                    continue
                ng = gij + c * dl
                if ng < best.get((ni, nj), INF):
                    best[(ni, nj)] = ng
                    came[(ni, nj)] = (i, j)
                    dx, dy = abs(ni - ti), abs(nj - tj)
                    heapq.heappush(heap, (ng + h0 * (dx + dy - 0.5858 * min(dx, dy)), ni, nj))
        if not found and (si, sj) != (ti, tj):
            return None
        cells = [(ti, tj)]
        while cells[-1] in came:
            cells.append(came[cells[-1]])
        cells.reverse()
        pts = [self.to_world(i, j) for i, j in cells]
        pts[0] = list(start)
        pts[-1] = [goal[0], goal[1]] if exact_goal else g
        return self._smooth(pts, profile, max_grade)

    def segment_clear(self, a, b, profile, max_grade=None) -> bool:
        d = math.hypot(b[0] - a[0], b[1] - a[1])
        n = max(1, math.ceil(d / (self.size * 0.5)))
        cost = self.cost_field(profile, max_grade)
        for s in range(1, n):
            t = s / n
            i, j = self.to_cell(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
            if not np.isfinite(cost[j, i]):
                return False
        return True

    def _smooth(self, pts, profile, max_grade):
        if len(pts) <= 2:
            return pts
        out = [pts[0]]
        anchor = 0
        for i in range(2, len(pts)):
            if not self.segment_clear(pts[anchor], pts[i], profile, max_grade):
                out.append(pts[i - 1])
                anchor = i - 1
        out.append(pts[-1])
        return out


def _densify(path, step=400.0):
    out = [list(map(round, path[0]))]
    for (ax, ay), (bx, by) in zip(path[:-1], path[1:]):
        n = max(1, math.ceil(math.hypot(bx - ax, by - ay) / step))
        for s in range(1, n + 1):
            out.append([round(ax + (bx - ax) * s / n), round(ay + (by - ay) * s / n)])
    return out


def generate_corridors(terrain, zones: dict) -> list[dict]:
    """Three avenues of approach between the BLUE and RED zone centroids.

    The best tracked route first, then two lateral alternatives pushed apart by a proximity
    penalty so they explore different valleys/approaches.
    """
    blue, red = zones.get('BLUE'), zones.get('RED')
    if not blue or not red or len(blue) < 3 or len(red) < 3:
        return []
    terrain.set_corridors([], CORRIDOR_WIDTH_M)
    nav = NavGrid(terrain)
    a = polygon_centroid(blue)
    b = polygon_centroid(red)
    sep = math.hypot(b[0] - a[0], b[1] - a[1]) or 1.0
    nx, ny = -(b[1] - a[1]) / sep, (b[0] - a[0]) / sep
    lat = min(6000.0, sep * 0.3)
    ends = [(a, b), ((a[0] + nx * lat, a[1] + ny * lat), (b[0] + nx * lat, b[1] + ny * lat)),
            ((a[0] - nx * lat, a[1] - ny * lat), (b[0] - nx * lat, b[1] - ny * lat))]
    out: list[dict] = []
    J, I = np.mgrid[0:nav.rows, 0:nav.cols]
    WX, WY = nav.x0 + I * nav.size, nav.y0 + J * nav.size
    for frm, to in ends:
        pen = None
        if out:
            dmin = np.full(WX.shape, np.inf)
            for c in out:
                for (px, py), (qx, qy) in zip(c['path'][:-1], c['path'][1:]):
                    dx, dy = qx - px, qy - py
                    l2 = dx * dx + dy * dy or 1.0
                    t = np.clip(((WX - px) * dx + (WY - py) * dy) / l2, 0, 1)
                    dmin = np.minimum(dmin, np.hypot(WX - (px + t * dx), WY - (py + t * dy)))
            pen = np.where(dmin < 2500, 6 * (1 - dmin / 2500), 0)
        f = terrain.clamp_xy(*frm)
        g = terrain.clamp_xy(*to)
        path = nav.find_path(f, g, 'TRACKED', max_grade=48, penalty=pen, max_nodes=200000)
        if path and len(path) >= 2:
            out.append(dict(id=len(out), path=_densify(path), width_m=CORRIDOR_WIDTH_M))
    if not out:
        out.append(dict(id=0, path=_densify([list(a), list(b)]), width_m=CORRIDOR_WIDTH_M))
    return out
