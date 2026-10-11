"""Formation shapes → slot offsets [right, forward] relative to the leader."""
from __future__ import annotations

import math

from .geo import DEG


def formation_offsets(shape: str, n: int, spacing: float) -> list[tuple[float, float]]:
    out = []
    for i in range(n):
        k = math.ceil(i / 2)
        side = 0 if i == 0 else (1 if i % 2 == 1 else -1)
        if shape == 'WEDGE':
            out.append((side * k * spacing, -k * spacing * 0.8))
        elif shape == 'VEE':
            out.append((side * k * spacing, k * spacing * 0.8))
        elif shape == 'LINE':
            out.append((side * k * spacing, 0.0))
        elif shape == 'COLUMN':
            out.append((0.0, -i * spacing * 0.9))
        elif shape == 'ECHELON_L':
            out.append((-i * spacing * 0.8, -i * spacing * 0.8))
        elif shape == 'ECHELON_R':
            out.append((i * spacing * 0.8, -i * spacing * 0.8))
        elif shape == 'DIAMOND':
            pts = [(0, 0), (spacing, -spacing), (-spacing, -spacing), (0, -2 * spacing)]
            ring = i // 4
            px, py = pts[i % 4]
            out.append((px * (1 + ring * 0.6), py - ring * spacing * 2.2))
        elif shape == 'RING':
            if i == 0:
                out.append((0.0, 0.0))
            else:
                a = (i - 1) / max(1, n - 1) * 360 * DEG
                r = spacing * max(1.2, (n - 1) / 3.2)
                out.append((math.sin(a) * r, math.cos(a) * r))
        else:
            out.append((side * k * spacing, 0.0))
    return out


def offset_to_world(off: tuple[float, float], heading: float) -> tuple[float, float]:
    h = heading * DEG
    fx, fy = math.sin(h), math.cos(h)
    rx, ry = math.cos(h), -math.sin(h)
    return off[0] * rx + off[1] * fx, off[0] * ry + off[1] * fy
