"""Local tangent-plane geometry.

The engine works in metres on a local east/north plane centred on the AO origin:
x = east, y = north, z = metres above sea level. Headings are compass degrees
(0 = north, 90 = east, clockwise).
"""
from __future__ import annotations

import math
import random

EARTH_R = 6_371_008.8
REFRACTION_K = 4 / 3
DEG = math.pi / 180


def m_per_deg_lat(lat: float) -> float:
    p = lat * DEG
    return 111132.92 - 559.82 * math.cos(2 * p) + 1.175 * math.cos(4 * p)


def m_per_deg_lon(lat: float) -> float:
    p = lat * DEG
    return 111412.84 * math.cos(p) - 93.5 * math.cos(3 * p)


def to_local(origin: tuple[float, float], lon: float, lat: float) -> tuple[float, float]:
    lat0, lon0 = origin
    return (lon - lon0) * m_per_deg_lon(lat0), (lat - lat0) * m_per_deg_lat(lat0)


def to_geo(origin: tuple[float, float], x: float, y: float) -> tuple[float, float]:
    """Returns (lon, lat)."""
    lat0, lon0 = origin
    return lon0 + x / m_per_deg_lon(lat0), lat0 + y / m_per_deg_lat(lat0)


def norm360(d: float) -> float:
    return d % 360.0


def angle_diff(a: float, b: float) -> float:
    """Signed smallest difference b - a in degrees, (-180, 180]."""
    d = (b - a) % 360.0
    return d - 360.0 if d > 180.0 else d


def bearing(ax: float, ay: float, bx: float, by: float) -> float:
    return math.degrees(math.atan2(bx - ax, by - ay)) % 360.0


def heading_vec(h: float) -> tuple[float, float]:
    return math.sin(h * DEG), math.cos(h * DEG)


def clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def dist(ax: float, ay: float, bx: float, by: float) -> float:
    return math.hypot(bx - ax, by - ay)


def curvature_drop(d: float) -> float:
    """Drop of the earth surface below a tangent line at distance d, with refraction."""
    return d * d / (2 * EARTH_R * REFRACTION_K)


def point_in_polygon(x: float, y: float, poly: list) -> bool:
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def polygon_centroid(poly: list) -> tuple[float, float]:
    a = cx = cy = 0.0
    n = len(poly)
    j = n - 1
    for i in range(n):
        f = poly[j][0] * poly[i][1] - poly[i][0] * poly[j][1]
        a += f
        cx += (poly[j][0] + poly[i][0]) * f
        cy += (poly[j][1] + poly[i][1]) * f
        j = i
    if abs(a) < 1e-9:
        return sum(p[0] for p in poly) / max(1, n), sum(p[1] for p in poly) / max(1, n)
    return cx / (3 * a), cy / (3 * a)


def polygon_area(poly: list) -> float:
    a = 0.0
    j = len(poly) - 1
    for i in range(len(poly)):
        a += poly[j][0] * poly[i][1] - poly[i][0] * poly[j][1]
        j = i
    return abs(a / 2)


def polygon_bounds(poly: list) -> tuple[float, float, float, float]:
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


def dist_to_segment(x, y, ax, ay, bx, by) -> float:
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy or 1.0
    t = clamp(((x - ax) * dx + (y - ay) * dy) / l2, 0.0, 1.0)
    return math.hypot(x - (ax + t * dx), y - (ay + t * dy))


def dist_to_polyline(x: float, y: float, line: list) -> float:
    best = math.inf
    for i in range(len(line) - 1):
        d = dist_to_segment(x, y, line[i][0], line[i][1], line[i + 1][0], line[i + 1][1])
        if d < best:
            best = d
    return best


def random_point_in_polygon(rng: random.Random, poly: list, tries: int = 200) -> tuple[float, float]:
    minx, miny, maxx, maxy = polygon_bounds(poly)
    for _ in range(tries):
        x, y = rng.uniform(minx, maxx), rng.uniform(miny, maxy)
        if point_in_polygon(x, y, poly):
            return x, y
    return polygon_centroid(poly)


def erf(x: float) -> float:
    return math.erf(x)


def grid_ref(x: float, y: float, half_m: float) -> str:
    """Numbered grid reference: 100 m easting/northing from the AO south-west corner."""
    e = max(0, int((x + half_m) // 100))
    n = max(0, int((y + half_m) // 100))
    return f'{e:03d} {n:03d}'
