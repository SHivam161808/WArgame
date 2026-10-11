"""Theatres (5 seasons/biomes), weather, time of day and per-cell ground classification."""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict

import numpy as np

from .geo import DEG

GROUND_TYPES = ['ROAD', 'GRASS', 'SAND', 'MUD', 'SNOW', 'ICE', 'ROCK', 'SALT', 'MARSH', 'WATER']
G_IDX = {g: i for i, g in enumerate(GROUND_TYPES)}


@dataclass
class Theatre:
    id: str
    name: str
    region: str
    season: str
    biome: str  # DESERT | MONSOON | MOUNTAIN | ICE | MARSH
    lat: float  # point on the international boundary / LoC used as AO origin
    lon: float
    blue_bearing: float  # compass bearing from the line toward the Indian (BLUE) side
    weather: list
    default_weather: str
    water_level: float | None
    flat_elevation: float
    temp_c: float
    description: str
    palette: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


THEATRES: list[Theatre] = [
    Theatre('thar-longewala', 'Thar Desert — Longewala', 'Jaisalmer sector, Rajasthan', 'Summer', 'DESERT',
            27.5808, 70.0012, 125, ['CLEAR', 'DUST', 'HAZE'], 'CLEAR', None, 120, 44,
            'Open dune sea with long sight lines. Sand slows tracked vehicles, every move kicks up a dust plume '
            'that betrays position, and midday heat shimmer degrades long-range gunnery.',
            {'tint': [1.08, 0.98, 0.84], 'fog': 0.00012, 'saturation': 1.05}),
    Theatre('punjab-asal-uttar', 'Punjab Plains — Asal Uttar', 'Khem Karan sector, Punjab', 'Monsoon', 'MONSOON',
            31.1709, 74.5071, 82, ['RAIN', 'STORM', 'CLEAR', 'FOG'], 'RAIN', None, 190, 29,
            'Flat, irrigated farmland soaked by the monsoon. Low ground turns to mud that bogs down heavy armour; '
            'rain cuts visibility and the corridors become the only fast going.',
            {'tint': [0.86, 0.92, 0.9], 'fog': 0.00028, 'saturation': 0.82}),
    Theatre('kashmir-uri', 'Kashmir LoC — Uri', 'Baramulla sector, Jammu & Kashmir', 'Autumn', 'MOUNTAIN',
            34.1605, 73.9744, 152, ['CLEAR', 'FOG', 'RAIN'], 'CLEAR', None, 1800, 12,
            'Steep forested ridges along the Line of Control. Valleys force armour onto a few corridors, ridgelines '
            'block sight lines and dead ground offers excellent concealment. Air and hover assets shine here.',
            {'tint': [1.02, 0.96, 0.88], 'fog': 0.00016, 'saturation': 0.95}),
    Theatre('ladakh-dras', 'Kargil–Dras — Winter Ice', 'Dras sector, Ladakh', 'Winter', 'ICE',
            34.5296, 75.8255, 134, ['SNOW', 'BLIZZARD', 'CLEAR'], 'SNOW', None, 3300, -18,
            'High-altitude snowfields above 3,000 m. Thin air cuts engine power, ice robs traction on slopes, cold '
            'raises fuel burn and blizzards reduce visibility to a few hundred metres.',
            {'tint': [0.9, 0.96, 1.1], 'fog': 0.0002, 'saturation': 0.7}),
    Theatre('kutch-rann', 'Rann of Kutch — Coastal Marsh', 'Great Rann, Gujarat', 'Post-monsoon', 'MARSH',
            24.2586, 69.2062, 160, ['HAZE', 'FOG', 'CLEAR'], 'HAZE', 4.0, 3, 32,
            'Vast salt flats cut by tidal creeks and marsh. Hard salt pan is fast going, marsh is slow, and open '
            'water can only be crossed by amphibious and hover units.',
            {'tint': [1.04, 1.02, 0.98], 'fog': 0.00022, 'saturation': 0.9}),
]
THEATRE_BY_ID = {t.id: t for t in THEATRES}


def theatre_by_id(tid: str) -> Theatre:
    return THEATRE_BY_ID.get(tid, THEATRES[0])


def default_zones(t: Theatre, ao_half_km: float) -> dict:
    """14 km × 8 km deployment boxes either side of the line, in local metres."""
    depth = min(9000, ao_half_km * 1000 * 0.5)
    half = min(7000, ao_half_km * 1000 * 0.45)
    near, far = 2500, 2500 + depth

    def make(sign):
        b = (t.blue_bearing + (0 if sign > 0 else 180)) * DEG
        fx, fy = math.sin(b), math.cos(b)  # away from the line
        lx, ly = math.cos(b), -math.sin(b)  # along the line
        p = lambda along, out: [round(lx * along + fx * out), round(ly * along + fy * out)]
        return [p(-half, near), p(half, near), p(half, far), p(-half, far)]

    return {'BLUE': make(1), 'RED': make(-1)}


WEATHER = {
    'CLEAR': dict(label='Clear', visibility=1.0, thermal=1.0, dispersion=1.0, traction=1.0, fuel=1.0, dust=1.0),
    'RAIN': dict(label='Rain', visibility=0.62, thermal=0.85, dispersion=1.12, traction=0.85, fuel=1.05, dust=0.0),
    'STORM': dict(label='Thunderstorm', visibility=0.42, thermal=0.7, dispersion=1.3, traction=0.75, fuel=1.08, dust=0.0),
    'SNOW': dict(label='Snowfall', visibility=0.55, thermal=0.85, dispersion=1.12, traction=0.85, fuel=1.15, dust=0.0),
    'BLIZZARD': dict(label='Blizzard', visibility=0.28, thermal=0.6, dispersion=1.4, traction=0.7, fuel=1.25, dust=0.0),
    'DUST': dict(label='Dust storm', visibility=0.38, thermal=0.75, dispersion=1.35, traction=0.95, fuel=1.1, dust=1.6),
    'FOG': dict(label='Fog', visibility=0.35, thermal=0.8, dispersion=1.05, traction=1.0, fuel=1.0, dust=0.4),
    'HAZE': dict(label='Haze / heat shimmer', visibility=0.75, thermal=0.9, dispersion=1.15, traction=1.0, fuel=1.0, dust=1.0),
}

TIME_OF_DAY = {
    'DAWN': dict(label='Dawn', hour=6.2),
    'DAY': dict(label='Day', hour=12.5),
    'EVENING': dict(label='Evening', hour=17.6),
    'NIGHT': dict(label='Night', hour=23.5),
    'DYNAMIC': dict(label='Dynamic (day/night cycle)', hour=5.5),
}


def battle_hour(tod: str, sim_time_s: float) -> float:
    """Local solar hour; DYNAMIC runs one game minute per sim second (24 min per day)."""
    if tod == 'DYNAMIC':
        return (5.5 + sim_time_s / 60.0) % 24.0
    return TIME_OF_DAY.get(tod, TIME_OF_DAY['DAY'])['hour']


def light_at_hour(h: float) -> float:
    """Optical light multiplier 0.3 (night) … 1.0 (day)."""
    sun = math.sin((h - 6) / 12 * math.pi)
    if sun > 0.25:
        return 1.0
    if sun < -0.2:
        return 0.3
    return 0.3 + (sun + 0.2) / 0.45 * 0.7


# ─── Deterministic value noise (vectorised) ──────────────────────────────────

def _hash(ix: np.ndarray, iy: np.ndarray, seed: int) -> np.ndarray:
    n = (ix.astype(np.int64) * 374761393 + iy.astype(np.int64) * 668265263 + seed * 2147483647) & 0xFFFFFFFF
    n = ((n ^ (n >> 13)) * 1274126177) & 0xFFFFFFFF
    return ((n ^ (n >> 16)) & 0xFFFFFFFF) / 4294967296.0


def value_noise(x: np.ndarray, y: np.ndarray, scale: float, seed: int = 1337) -> np.ndarray:
    fx, fy = x / scale, y / scale
    ix, iy = np.floor(fx), np.floor(fy)
    tx, ty = fx - ix, fy - iy
    s = lambda t: t * t * (3 - 2 * t)
    a, b = _hash(ix, iy, seed), _hash(ix + 1, iy, seed)
    c, d = _hash(ix, iy + 1, seed), _hash(ix + 1, iy + 1, seed)
    u, v = s(tx), s(ty)
    return a + (b - a) * u + (c - a) * v + (a - b - c + d) * u * v


def fbm(x, y, scale, seed=1337):
    return (0.55 * value_noise(x, y, scale, seed) + 0.3 * value_noise(x, y, scale / 2.3, seed + 7)
            + 0.15 * value_noise(x, y, scale / 5.1, seed + 13))


def classify_ground(biome: str, elev, slope, water_depth, x, y, relief) -> np.ndarray:
    """Ground type index per cell (arrays in, uint8 array out)."""
    n = fbm(x, y, 1800.0)
    g = np.full(elev.shape, G_IDX['GRASS'], dtype=np.uint8)
    if biome == 'DESERT':
        g[:] = G_IDX['SAND']
        g[(n > 0.72) & (slope < 4)] = G_IDX['ROCK']
        g[slope > 18] = G_IDX['ROCK']
    elif biome == 'MONSOON':
        g[n > 0.8] = G_IDX['MARSH']
        g[(relief < -1.2) | (n < 0.25)] = G_IDX['MUD']
    elif biome == 'MOUNTAIN':
        g[(relief < -15) & (n < 0.35)] = G_IDX['MUD']
        g[slope > 24] = G_IDX['ROCK']
        g[elev > 3600] = G_IDX['SNOW']
    elif biome == 'ICE':
        g[:] = G_IDX['SNOW']
        g[(slope < 7) & (n > 0.62)] = G_IDX['ICE']
        g[slope > 32] = G_IDX['ROCK']
    elif biome == 'MARSH':
        g[:] = G_IDX['SALT']
        g[n < 0.2] = G_IDX['MUD']
        g[water_depth > -1.2] = G_IDX['MARSH']
    g[water_depth > 0.05] = G_IDX['WATER']
    return g


CONCEAL_BASE = {'DESERT': 0.08, 'MONSOON': 0.3, 'MOUNTAIN': 0.42, 'ICE': 0.12, 'MARSH': 0.18}


def concealment(biome: str, ground, slope, relief, x, y) -> np.ndarray:
    """Concealment 0..0.9 from biome, roughness, dead ground and vegetation."""
    veg = fbm(x, y, 900.0, 99)
    c = CONCEAL_BASE[biome] + np.minimum(0.3, slope / 60) + np.minimum(0.25, np.maximum(0, -relief) / 40)
    vegetated = (ground == G_IDX['GRASS']) | (ground == G_IDX['MARSH'])
    c = np.where(vegetated, c + veg * 0.25, c)
    open_ground = (ground == G_IDX['SALT']) | (ground == G_IDX['ICE']) | (ground == G_IDX['WATER'])
    c = np.where(open_ground, c * 0.4, c)
    return np.clip(c, 0, 0.9).astype(np.float32)
