"""Village Skirmish map: a procedural Indian village on a 600 m play area.

One seed → roads, a lake, houses (huts, houses, big houses, sheds) with real door and window openings, a school
compound, haystacks and mud walls for cover, parked vehicles, loot spots and team spawns. The same wall geometry
drives movement collision, line of sight, bullets and the bots' walking graph, and is sent to the client so the
3D village matches the simulation exactly.

Wall piece = [ax, ay, bx, by, zb, z0, z1, kind]: a vertical slab from (ax, ay) to (bx, by), WALL_T thick, from
zb + z0 to zb + z1 metres ASL (zb = the building's floor level). Door and window openings are simply gaps
between pieces (a lintel above a door, a sill below and a lintel above a window), so you can walk through
doors and see / shoot through windows.
"""
from __future__ import annotations

import heapq
import math
import random

import numpy as np

PLAY_HALF = 300.0    # playable square ±300 m (600 m × 600 m)
WORLD_HALF = 800.0   # terrain extent: scenery and hills beyond the play area
CELL = 2.0           # terrain grid (m)
WALL_T = 0.25
DOOR_W, DOOR_H = 1.3, 2.15
WIN_W, WIN_Z0, WIN_Z1 = 1.2, 0.95, 1.95
BLOCK_Z = 1.0        # pieces starting below this height stop people and vehicles
BODY_R = 0.3         # soldier collision radius
DRY = 0.6            # land outside the lake stays at least this far above the water
HASH = 8.0           # spatial hash cell for walls (m)
KINDS = ['wall', 'low', 'hay', 'compound']
SPAWN = {'BLUE': (-245.0, -240.0), 'RED': (245.0, 240.0)}

# building templates: width × depth × height; front face (local -y) faces the road
BUILDINGS = {
    'hut': dict(w=6.0, d=5.0, h=3.1),
    'house': dict(w=9.0, d=7.0, h=3.3),
    'big': dict(w=13.0, d=9.0, h=3.6),
    'shed': dict(w=8.0, d=6.0, h=2.9),
    'school': dict(w=32.0, d=11.0, h=4.1),
}
PALETTE = ['#d9b98c', '#e6d3b1', '#c98f6b', '#b9c7c9', '#e3b9b0', '#cfc48f', '#d7d2c8', '#a8603f']
LOOT_WEIGHTS = [('pistol-p9', 10), ('smg-v9', 15), ('rifle-m4', 18), ('rifle-ak', 12), ('dmr-sks', 7), ('sniper-awm', 3),
                ('shotgun-s12', 10), ('at-rpg', 3), ('ammo', 14), ('medkit', 11), ('vest', 4), ('helmet', 4)]


# ── geometry helpers ───────────────────────────────────────────────────────

def _rot(rot, lx, ly, cx, cy):
    c, s = math.cos(rot), math.sin(rot)
    return cx + c * lx - s * ly, cy + s * lx + c * ly


def _seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy or 1e-9
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / l2))
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(px - cx, py - cy), cx, cy


def _cross(ax, ay, bx, by):
    return ax * by - ay * bx


def _seg_hit(px, py, rx, ry, qx, qy, sx, sy):
    """Intersection of p + t·r and q + u·s (t, u ∈ [0, 1]); returns t or None."""
    den = _cross(rx, ry, sx, sy)
    if abs(den) < 1e-12:
        return None
    wx, wy = qx - px, qy - py
    t = _cross(wx, wy, sx, sy) / den
    u = _cross(wx, wy, rx, ry) / den
    if 0.0 <= t <= 1.0 and 0.0 <= u <= 1.0:
        return t
    return None


def _polyline_dist(x, y, pts):
    best = 1e9
    for (ax, ay), (bx, by) in zip(pts[:-1], pts[1:]):
        d, _, _ = _seg_dist(x, y, ax, ay, bx, by)
        best = min(best, d)
    return best


def _densify(pts, step=10.0):
    out = [pts[0]]
    for (ax, ay), (bx, by) in zip(pts[:-1], pts[1:]):
        n = max(1, int(math.hypot(bx - ax, by - ay) / step))
        for k in range(1, n + 1):
            out.append((ax + (bx - ax) * k / n, ay + (by - ay) * k / n))
    return [[round(x, 2), round(y, 2)] for x, y in out]


def lake_radius(lake, theta):
    return lake['r'] * (1 + lake['a3'] * math.sin(3 * theta + lake['p3']) + lake['a5'] * math.sin(5 * theta + lake['p5']))


def lake_rel(lake, x, y):
    """Distance from the lake centre relative to the shore radius (< 1 = in the water)."""
    dx, dy = x - lake['cx'], y - lake['cy']
    return math.hypot(dx, dy) / lake_radius(lake, math.atan2(dy, dx))


# ── wall construction ─────────────────────────────────────────────────────

def _wall_line(out, a, b, h, zb, openings=(), kind=0):
    """Wall from a to b with openings [(s_centre, width, 'door'|'window'), …] measured along the wall."""
    ax, ay = a
    bx, by = b
    L = math.hypot(bx - ax, by - ay)
    if L < 1e-6:
        return
    ux, uy = (bx - ax) / L, (by - ay) / L
    P = lambda s: (ax + ux * s, ay + uy * s)
    cur = 0.0
    for sc, w, typ in sorted(openings):
        s0, s1 = max(cur, sc - w / 2), min(L, sc + w / 2)
        if s0 > cur + 0.05:
            out.append([*P(cur), *P(s0), zb, 0.0, h, kind])
        if typ == 'door':
            out.append([*P(s0), *P(s1), zb, DOOR_H, h, kind])
        else:
            out.append([*P(s0), *P(s1), zb, 0.0, WIN_Z0, kind])
            out.append([*P(s0), *P(s1), zb, WIN_Z1, h, kind])
        cur = s1
    if L - cur > 0.05:
        out.append([*P(cur), *P(L), zb, 0.0, h, kind])


def _building(kind, cx, cy, rot, zb, rng):
    """Walls, doors (with outward normals), rooms and roof of one building in world coordinates."""
    t = BUILDINGS[kind]
    w, d, h = t['w'], t['d'], t['h']
    hw, hd = w / 2, d / 2
    W = lambda lx, ly: _rot(rot, lx, ly, cx, cy)
    walls, doors, rooms = [], [], []
    front_n = _rot(rot, 0, -1, 0, 0)
    back_n = _rot(rot, 0, 1, 0, 0)
    left_n = _rot(rot, -1, 0, 0, 0)
    right_n = _rot(rot, 1, 0, 0, 0)

    def door(lx, ly, n):
        x, y = W(lx, ly)
        doors.append([round(x, 2), round(y, 2), round(n[0], 3), round(n[1], 3)])

    if kind == 'hut':
        _wall_line(walls, W(-hw, -hd), W(hw, -hd), h, zb, [(hw, DOOR_W, 'door')])
        door(0, -hd, front_n)
        _wall_line(walls, W(hw, -hd), W(hw, hd), h, zb, [(hd, WIN_W, 'window')])
        _wall_line(walls, W(hw, hd), W(-hw, hd), h, zb, [(hw, WIN_W, 'window')])
        _wall_line(walls, W(-hw, hd), W(-hw, -hd), h, zb)
        rooms.append(W(0, 0.3))
    elif kind == 'house':
        dx = -hw + 2.5
        _wall_line(walls, W(-hw, -hd), W(hw, -hd), h, zb, [(2.5, DOOR_W, 'door'), (w - 2.3, WIN_W, 'window')])
        door(dx, -hd, front_n)
        _wall_line(walls, W(hw, -hd), W(hw, hd), h, zb, [(d / 2, WIN_W, 'window')])
        _wall_line(walls, W(hw, hd), W(-hw, hd), h, zb, [(2.2, WIN_W, 'window'), (w - 2.5, WIN_W, 'window')])
        _wall_line(walls, W(-hw, hd), W(-hw, -hd), h, zb, [(d / 2, WIN_W, 'window')])
        _wall_line(walls, W(0.5, -hd), W(0.5, hd), h, zb, [(d / 2, DOOR_W, 'door')])  # inner wall
        rooms += [W((-hw + 0.5) / 2, 0), W((hw + 0.5) / 2, 0)]
    elif kind == 'big':
        _wall_line(walls, W(-hw, -hd), W(hw, -hd), h, zb, [(2.2, DOOR_W, 'door'), (5.5, WIN_W, 'window'), (w - 3.0, DOOR_W, 'door')])
        door(-hw + 2.2, -hd, front_n)
        door(hw - 3.0, -hd, front_n)
        _wall_line(walls, W(hw, -hd), W(hw, hd), h, zb, [(2.3, WIN_W, 'window'), (d - 2.3, WIN_W, 'window')])
        _wall_line(walls, W(hw, hd), W(-hw, hd), h, zb, [(2.5, WIN_W, 'window'), (w / 2, WIN_W, 'window'), (w - 2.5, WIN_W, 'window')])
        _wall_line(walls, W(-hw, hd), W(-hw, -hd), h, zb, [(d / 2, DOOR_W, 'door')])
        door(-hw, 0, left_n)
        _wall_line(walls, W(-1.5, -hd), W(-1.5, hd), h, zb, [(d - 2.0, DOOR_W, 'door')])  # inner walls
        _wall_line(walls, W(-1.5, 0.5), W(hw, 0.5), h, zb, [(3.0, DOOR_W, 'door')])
        rooms += [W((-hw - 1.5) / 2, 0), W((hw - 1.5) / 2, -hd / 2 + 0.2), W((hw - 1.5) / 2, hd / 2)]
    elif kind == 'shed':  # open front, three walls
        _wall_line(walls, W(hw, -hd), W(hw, hd), h, zb)
        _wall_line(walls, W(hw, hd), W(-hw, hd), h, zb, [(w / 2, WIN_W, 'window')])
        _wall_line(walls, W(-hw, hd), W(-hw, -hd), h, zb)
        door(0, -hd, front_n)
        rooms.append(W(0, 0.5))
    elif kind == 'school':
        ops = [(6.0, DOOR_W * 1.3, 'door'), (16.0, DOOR_W * 1.3, 'door'), (26.0, DOOR_W * 1.3, 'door')]
        ops += [(s, WIN_W * 1.4, 'window') for s in (2.5, 10.0, 13.0, 19.0, 22.5, 29.5)]
        _wall_line(walls, W(-hw, -hd), W(hw, -hd), h, zb, ops)
        for lx in (-10.0, 0.0, 10.0):
            door(lx, -hd, front_n)
        _wall_line(walls, W(hw, -hd), W(hw, hd), h, zb, [(d / 2, WIN_W, 'window')])
        _wall_line(walls, W(hw, hd), W(-hw, hd), h, zb, [(s, WIN_W * 1.4, 'window') for s in (3, 8, 13, 19, 24, 29)])
        _wall_line(walls, W(-hw, hd), W(-hw, -hd), h, zb, [(d / 2, WIN_W, 'window')])
        for lx in (-5.3, 5.3):  # classrooms, linked at the back
            _wall_line(walls, W(lx, -hd), W(lx, hd), h, zb, [(d - 2.0, DOOR_W, 'door')])
        rooms += [W(-10.5, 0.3), W(-10.5, -2.5), W(0, 0.3), W(0, -2.5), W(10.5, 0.3), W(10.5, -2.5)]
    return walls, doors, [[round(x, 2), round(y, 2)] for x, y in rooms]


def _rect_corners(cx, cy, w, d, rot, pad=0.0):
    hw, hd = w / 2 + pad, d / 2 + pad
    return [_rot(rot, sx * hw, sy * hd, cx, cy) for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]


def _rects_overlap(a, b):
    """Separating-axis test for two convex quads (corner lists)."""
    for poly in (a, b):
        for i in range(4):
            x1, y1 = poly[i]
            x2, y2 = poly[(i + 1) % 4]
            nx, ny = y2 - y1, x1 - x2
            pa = [nx * x + ny * y for x, y in a]
            pb = [nx * x + ny * y for x, y in b]
            if max(pa) < min(pb) or max(pb) < min(pa):
                return False
    return True


def _rect_points(corners, step=1.5):
    pts = []
    for i in range(4):
        (ax, ay), (bx, by) = corners[i], corners[(i + 1) % 4]
        n = max(1, int(math.hypot(bx - ax, by - ay) / step))
        pts += [(ax + (bx - ax) * k / n, ay + (by - ay) * k / n) for k in range(n)]
    return pts


def inside_rect(x, y, b, pad=0.0):
    c, s = math.cos(-b['rot']), math.sin(-b['rot'])
    lx, ly = c * (x - b['cx']) - s * (y - b['cy']), s * (x - b['cx']) + c * (y - b['cy'])
    return abs(lx) <= b['w'] / 2 + pad and abs(ly) <= b['d'] / 2 + pad


# ── layout ──────────────────────────────────────────────────────────────────

def generate(seed: int, base_elev: float = 200.0) -> dict:
    """The full village layout (JSON-serialisable)."""
    rng = random.Random(seed)
    j = lambda a: rng.uniform(-a, a)
    ph = [rng.uniform(0, 6.283) for _ in range(6)]
    elev = lambda x, y: _base_elevation(x, y, base_elev, ph)

    # roads: main east–west, north–south, and four lanes into the corners
    main = [(x, 10 + 14 * math.sin(x / 170 + ph[0])) for x in range(-800, 801, 40)]
    ns = [(-40 + 12 * math.sin(y / 140 + ph[1]), y) for y in range(-800, 801, 40)]
    lanes = [
        [(-40, 115 + j(10)), (60 + j(15), 135 + j(15)), (150 + j(20), 120 + j(20)), (210 + j(15), 60 + j(15))],
        [(-40, -110 + j(10)), (-150 + j(15), -125 + j(15)), (-215 + j(10), -215 + j(10)), (-250, -240)],
        [(100 + j(10), 12), (125 + j(15), -110 + j(15)), (235 + j(10), -190 + j(15))],
        [(-40, 55 + j(8)), (-160 + j(15), 75 + j(15)), (-225 + j(15), 190 + j(15))],
        [(150, 130), (190 + j(10), 215 + j(10)), (245, 240)],
    ]
    roads = [dict(path=_densify(main), width=8.0, name='main'), dict(path=_densify(ns), width=7.0, name='north-south')]
    roads += [dict(path=_densify(p), width=5.0, name=f'lane-{k + 1}') for k, p in enumerate(lanes)]
    road_dist = lambda x, y: min(_polyline_dist(x, y, r['path']) - r['width'] / 2 for r in roads)

    # lake: the open spot farthest from roads in the play area
    best = None
    for gx in range(-220, 221, 20):
        for gy in range(-220, 221, 20):
            if min(math.hypot(gx - sx, gy - sy) for sx, sy in SPAWN.values()) < 140:
                continue
            clear = road_dist(gx, gy)
            score = clear + rng.uniform(0, 8) - 0.05 * math.hypot(gx, gy)
            if best is None or score > best[0]:
                best = (score, gx, gy, clear)
    _, lx, ly, clear = best
    r = max(22.0, min(60.0, (clear - 12) / 1.25))
    lake = dict(cx=float(lx), cy=float(ly), r=round(r, 1), a3=round(rng.uniform(0.08, 0.16), 3), p3=round(rng.uniform(0, 6.28), 3),
                a5=round(rng.uniform(0.04, 0.09), 3), p5=round(rng.uniform(0, 6.28), 3))
    lake['level'] = round(min(elev(lx + math.cos(a) * r * 1.3, ly + math.sin(a) * r * 1.3) for a in np.linspace(0, 6.28, 24)) - 0.7, 2)
    raw_elev = elev
    elev = lambda x, y: max(raw_elev(x, y), lake['level'] + DRY)  # only the lake holds water

    buildings, walls, foot = [], [], []

    def site_ok(corners, pad_road=1.5, lake_pad=1.45):
        for c in foot:
            if _rects_overlap(corners, c):
                return False
        for x, y in _rect_points(corners) + [(sum(p[0] for p in corners) / 4, sum(p[1] for p in corners) / 4)]:
            if abs(x) > PLAY_HALF - 12 or abs(y) > PLAY_HALF - 12:
                return False
            if road_dist(x, y) < pad_road:
                return False
            if lake_rel(lake, x, y) < lake_pad:
                return False
            if min(math.hypot(x - sx, y - sy) for sx, sy in SPAWN.values()) < 40:
                return False
        return True

    def add_building(kind, cx, cy, rot, colour=None):
        t = BUILDINGS[kind]
        zb = round(elev(cx, cy) + 0.25, 2)
        ws, doors, rooms = _building(kind, cx, cy, rot, zb, rng)
        b = dict(id=f'b{len(buildings)}', k=kind, cx=round(cx, 2), cy=round(cy, 2), w=t['w'], d=t['d'], h=t['h'], rot=round(rot, 4),
                 zb=zb, col=colour or rng.choice(PALETTE), doors=doors, rooms=rooms)
        buildings.append(b)
        walls.extend(ws)
        return b

    # school compound on the main road near the crossroads
    school = None
    for k in sorted(range(len(roads[0]['path'])), key=lambda i: abs(roads[0]['path'][i][0] - (-150 + j(60)))):
        px, py = roads[0]['path'][k]
        if abs(px) > 230:
            continue
        nx_, ny_ = _normal(roads[0]['path'], k)
        side = rng.choice((1, -1))
        nx_, ny_ = nx_ * side, ny_ * side
        rot = math.atan2(-nx_, ny_)
        cw, cd = 50.0, 34.0
        ccx, ccy = px + nx_ * (4 + 2 + cd / 2), py + ny_ * (4 + 2 + cd / 2)
        corners = _rect_corners(ccx, ccy, cw, cd, rot)
        if not site_ok(corners, pad_road=0.5):
            continue
        foot.append(_rect_corners(ccx, ccy, cw, cd, rot, 1.0))
        zb = round(elev(ccx, ccy) + 0.05, 2)
        # compound wall (1.6 m — cover for a crouched soldier) with a 5 m gate facing the road
        W = lambda lx, ly: _rot(rot, lx, ly, ccx, ccy)
        hw, hd = cw / 2, cd / 2
        _wall_line(walls, W(-hw, -hd), W(hw, -hd), 1.6, zb, [(hw, 5.0, 'door')], kind=3)
        _wall_line(walls, W(hw, -hd), W(hw, hd), 1.6, zb, kind=3)
        _wall_line(walls, W(hw, hd), W(-hw, hd), 1.6, zb, [(hw + 14, 2.2, 'door')], kind=3)
        _wall_line(walls, W(-hw, hd), W(-hw, -hd), 1.6, zb, kind=3)
        sx, sy = W(0, hd - 11.0 / 2 - 3)
        school = add_building('school', sx, sy, rot, '#e8d9a8')
        school['compound'] = dict(cx=round(ccx, 2), cy=round(ccy, 2), w=cw, d=cd, rot=round(rot, 4), zb=zb,
                                  gate=[round(c, 2) for c in W(0, -hd)], gate_n=[round(-nx_, 3), round(-ny_, 3)],
                                  back_gate=[round(c, 2) for c in W(14, hd)])
        break

    # houses along every road
    weights = [('hut', 0.33), ('house', 0.37), ('big', 0.17), ('shed', 0.13)]
    for road in roads:
        path = road['path']
        k = rng.randrange(2)
        while k < len(path) - 1:
            px, py = path[k]
            if abs(px) < PLAY_HALF - 15 and abs(py) < PLAY_HALF - 15:
                core = math.hypot(px + 40, py) < 210
                for side in (1, -1):
                    for row in (0, 1):  # roadside row, and a second row behind it in the village core
                        if row and (not core or rng.random() < 0.55):
                            continue
                        if not row and rng.random() < (0.55 if not core else 0.2):
                            continue
                        kind = _pick(rng, weights)
                        t = BUILDINGS[kind]
                        nx_, ny_ = _normal(path, k)
                        nx_, ny_ = nx_ * side, ny_ * side
                        rot = math.atan2(-nx_, ny_) + rng.uniform(-0.08, 0.08)
                        off = road['width'] / 2 + rng.uniform(3.0, 6.0) + t['d'] / 2 + row * rng.uniform(13, 18)
                        sh = rng.uniform(-3, 3)
                        cx, cy = px + nx_ * off - ny_ * sh, py + ny_ * off + nx_ * sh
                        corners = _rect_corners(cx, cy, t['w'], t['d'], rot, 1.8)
                        if site_ok(corners):
                            foot.append(corners)
                            add_building(kind, cx, cy, rot)
            k += rng.choice((2, 2, 3))

    # farm cover: haystacks (round-ish, 2.4 m) and mud walls (1.1 m)
    props = []
    tries = 0
    while len([p for p in props if p['k'] == 'hay']) < 26 and tries < 600:
        tries += 1
        x, y = rng.uniform(-PLAY_HALF + 15, PLAY_HALF - 15), rng.uniform(-PLAY_HALF + 15, PLAY_HALF - 15)
        r = rng.uniform(1.4, 2.0)
        corners = _rect_corners(x, y, 2 * r, 2 * r, 0, 1.5)
        if not site_ok(corners, pad_road=2.5, lake_pad=1.3):
            continue
        foot.append(corners)
        zb = round(elev(x, y), 2)
        n = 6
        pts = [(x + math.cos(a) * r, y + math.sin(a) * r) for a in np.linspace(0, 2 * math.pi, n + 1)]
        for a, b in zip(pts[:-1], pts[1:]):
            walls.append([*a, *b, zb, 0.0, 2.3, 2])
        props.append(dict(k='hay', x=round(x, 2), y=round(y, 2), r=round(r, 2), zb=zb))
    tries = 0
    while len([p for p in props if p['k'] == 'mudwall']) < 14 and tries < 600:
        tries += 1
        x, y = rng.uniform(-PLAY_HALF + 15, PLAY_HALF - 15), rng.uniform(-PLAY_HALF + 15, PLAY_HALF - 15)
        L, rot = rng.uniform(6, 14), rng.uniform(0, math.pi)
        corners = _rect_corners(x, y, L, 0.6, rot, 1.5)
        if not site_ok(corners, pad_road=2.5, lake_pad=1.3):
            continue
        foot.append(corners)
        zb = round(elev(x, y), 2)
        a, b = _rot(rot, -L / 2, 0, x, y), _rot(rot, L / 2, 0, x, y)
        walls.append([*a, *b, zb, 0.0, 1.1, 1])
        props.append(dict(k='mudwall', x=round(x, 2), y=round(y, 2), len=round(L, 2), rot=round(rot, 4), zb=zb))

    # parked vehicles at the roadside; tanks in the open, mid-map
    vehicles = []
    candidates = []
    for road in roads:
        p = road['path']
        for k in range(1, len(p) - 1, 3):
            x, y = p[k]
            if abs(x) < PLAY_HALF - 25 and abs(y) < PLAY_HALF - 25:
                candidates.append((road, k))
    rng.shuffle(candidates)
    want = ['car'] * 5 + ['bike'] * 5 + ['jeep'] * 2
    for road, k in candidates:
        if not want:
            break
        p = road['path']
        nx_, ny_ = _normal(p, k)
        side = rng.choice((1, -1))
        off = road['width'] / 2 + 1.8
        x, y = p[k][0] + nx_ * off * side, p[k][1] + ny_ * off * side
        tx, ty = p[k + 1][0] - p[k - 1][0], p[k + 1][1] - p[k - 1][1]
        heading = (math.degrees(math.atan2(tx, ty)) + rng.choice((0, 180))) % 360
        if any(math.hypot(x - v['x'], y - v['y']) < 25 for v in vehicles) or lake_rel(lake, x, y) < 1.3:
            continue
        if any(_rects_overlap(_rect_corners(x, y, 5, 5, 0), c) for c in foot):
            continue
        if min(math.hypot(x - sx, y - sy) for sx, sy in SPAWN.values()) < 30 and want[0] != 'bike':
            continue
        vehicles.append(dict(k=want.pop(0), x=round(x, 2), y=round(y, 2), h=round(heading, 1)))
    for side in (1, -1):  # one tank each side of the map centre, in the open
        for _ in range(200):
            x, y = rng.uniform(-150, 150) * side, rng.uniform(-150, 150) * side * -1
            if road_dist(x, y) < 6 or lake_rel(lake, x, y) < 1.4 or any(_rects_overlap(_rect_corners(x, y, 12, 12, 0), c) for c in foot):
                continue
            vehicles.append(dict(k='tank', x=round(x, 2), y=round(y, 2), h=round(rng.uniform(0, 360), 1)))
            break
    # a bike at each spawn so nobody has a long walk
    for s, (sx, sy) in SPAWN.items():
        vehicles.append(dict(k='bike', x=sx + 6, y=sy + 4, h=45.0 if s == 'BLUE' else 225.0))

    # loot: every room, the school, sheds and a few spots in the open
    loot = []
    def drop(x, y, z):
        k = _pick(rng, LOOT_WEIGHTS)
        loot.append(dict(id=f'i{len(loot)}', k=k, x=round(x + j(0.8), 2), y=round(y + j(0.8), 2), z=round(z, 2)))
    for b in buildings:
        for (rx, ry) in b['rooms']:
            n = (1 if rng.random() < 0.8 else 0) if b['k'] == 'school' else (1 if rng.random() < 0.55 else 0) + (1 if rng.random() < 0.1 else 0)
            for _ in range(n):
                drop(rx, ry, b['zb'] + 0.05)
    for p in props:
        if rng.random() < 0.3:
            ang = rng.uniform(0, 6.28)
            x, y = p['x'] + math.cos(ang) * 3.2, p['y'] + math.sin(ang) * 3.2
            drop(x, y, elev(x, y) + 0.05)
    for s, (sx, sy) in SPAWN.items():  # a starter weapon and ammo beside each spawn
        for k, kind in enumerate(('smg-v9', 'rifle-m4', 'shotgun-s12', 'rifle-ak', 'ammo', 'medkit')):
            x, y = sx - 9 + 3.5 * k, sy - 6 - (k % 2) * 2
            loot.append(dict(id=f'i{len(loot)}', k=kind, x=round(x, 2), y=round(y, 2), z=round(elev(x, y) + 0.05, 2)))

    spawns = {}
    for s, (sx, sy) in SPAWN.items():
        spawns[s] = [[round(sx + dx, 2), round(sy + dy, 2)] for dx, dy in ((0, 0), (4, 2), (-3, 4), (2, -4), (-5, -2), (6, -3))]
    return dict(seed=seed, play_half=PLAY_HALF, world_half=WORLD_HALF, base=base_elev, phase=[round(p, 4) for p in ph],
                roads=roads, lake=lake, buildings=buildings, walls=[[round(v, 3) if isinstance(v, float) else v for v in w] for w in walls],
                props=props, vehicles=vehicles, loot=loot, spawns=spawns, wall_t=WALL_T)


def _pick(rng, weights):
    tot = sum(w for _, w in weights)
    r = rng.uniform(0, tot)
    for k, w in weights:
        r -= w
        if r <= 0:
            return k
    return weights[-1][0]


def _normal(path, k):
    a = path[max(0, k - 1)]
    b = path[min(len(path) - 1, k + 1)]
    tx, ty = b[0] - a[0], b[1] - a[1]
    L = math.hypot(tx, ty) or 1
    return -ty / L, tx / L


def _base_elevation(x, y, base, ph):
    e = base + 1.6 * math.sin(x / 97 + ph[2]) * math.cos(y / 131 + ph[3]) + 1.1 * math.sin((x + y) / 61 + ph[4])
    r = max(abs(x), abs(y))
    return e + max(0.0, min(1.0, (r - 420) / 380)) ** 2 * 32


# ── terrain ─────────────────────────────────────────────────────────────────

def terrain_elevation(layout) -> np.ndarray:
    """Elevation grid (rows = south→north) with the lake basin and flattened building pads."""
    n = int(round(2 * WORLD_HALF / CELL)) + 1
    xs = np.linspace(-WORLD_HALF, WORLD_HALF, n)
    X, Y = np.meshgrid(xs, xs)
    ph, base = layout['phase'], layout['base']
    E = base + 1.6 * np.sin(X / 97 + ph[2]) * np.cos(Y / 131 + ph[3]) + 1.1 * np.sin((X + Y) / 61 + ph[4])
    R = np.maximum(np.abs(X), np.abs(Y))
    E = E + np.clip((R - 420) / 380, 0, 1) ** 2 * 32
    lake = layout['lake']
    dx, dy = X - lake['cx'], Y - lake['cy']
    th = np.arctan2(dy, dx)
    rr = np.hypot(dx, dy) / (lake['r'] * (1 + lake['a3'] * np.sin(3 * th + lake['p3']) + lake['a5'] * np.sin(5 * th + lake['p5'])))
    wl = lake['level']
    bed = wl - 3.2 * np.clip(1 - rr ** 2, 0, 1) - 0.25
    shore = np.clip((rr - 1.0) / 0.45, 0, 1)
    E = np.maximum(E, wl + DRY)  # no puddles anywhere but the lake
    E = np.where(rr < 1.0, bed, np.where(rr < 1.45, (wl + 0.15) * (1 - shore) + E * shore, E))
    # flat pads under buildings and the school compound
    def pad(cx, cy, w, d, rot, z):
        hw, hd = w / 2 + 2.5, d / 2 + 2.5
        c, s = math.cos(-rot), math.sin(-rot)
        r = math.hypot(hw, hd)
        i0, i1 = _idx(cx - r), _idx(cx + r) + 1
        j0, j1 = _idx(cy - r), _idx(cy + r) + 1
        sub_x, sub_y = X[j0:j1, i0:i1], Y[j0:j1, i0:i1]
        lx = c * (sub_x - cx) - s * (sub_y - cy)
        ly = s * (sub_x - cx) + c * (sub_y - cy)
        m = (np.abs(lx) <= hw) & (np.abs(ly) <= hd)
        E[j0:j1, i0:i1][m] = z
    for b in layout['buildings']:
        if b.get('compound'):
            cp = b['compound']
            pad(cp['cx'], cp['cy'], cp['w'], cp['d'], cp['rot'], cp['zb'])
    for b in layout['buildings']:
        pad(b['cx'], b['cy'], b['w'], b['d'], b['rot'], b['zb'] - 0.25 if b['k'] != 'school' else b['zb'] - 0.05)
    return E.astype(np.float32)


def _idx(v):
    return int(max(0, min(int(round(2 * WORLD_HALF / CELL)), round((v + WORLD_HALF) / CELL))))


def apply_ground(grid):
    """Called from TerrainGrid._derive: roads and building pads are ROAD (no trees, dirt texture)."""
    from .theatres import G_IDX
    lay = grid.meta['village']
    n = grid.cols
    xs = grid.x0 + np.arange(n) * grid.cell
    X, Y = np.meshgrid(xs, grid.y0 + np.arange(grid.rows) * grid.cell)
    road = np.zeros(X.shape, dtype=bool)
    for r in lay['roads']:
        hw = r['width'] / 2
        for (ax, ay), (bx, by) in zip(r['path'][:-1], r['path'][1:]):
            i0, i1 = _idx(min(ax, bx) - hw - 2), _idx(max(ax, bx) + hw + 2) + 1
            j0, j1 = _idx(min(ay, by) - hw - 2), _idx(max(ay, by) + hw + 2) + 1
            px, py = X[j0:j1, i0:i1], Y[j0:j1, i0:i1]
            dx, dy = bx - ax, by - ay
            l2 = dx * dx + dy * dy or 1.0
            t = np.clip(((px - ax) * dx + (py - ay) * dy) / l2, 0, 1)
            d = np.hypot(px - (ax + t * dx), py - (ay + t * dy))
            road[j0:j1, i0:i1] |= d <= hw
    water = grid.ground == G_IDX['WATER']
    grid.ground[road & ~water] = G_IDX['ROAD']
    for b in lay['buildings']:
        for (cx, cy, w, d, rot) in ([(b['compound']['cx'], b['compound']['cy'], b['compound']['w'], b['compound']['d'], b['compound']['rot'])]
                                    if b.get('compound') else []) + [(b['cx'], b['cy'], b['w'], b['d'], b['rot'])]:
            r = math.hypot(w, d) / 2 + 2
            i0, i1 = _idx(cx - r), _idx(cx + r) + 1
            j0, j1 = _idx(cy - r), _idx(cy + r) + 1
            c, s = math.cos(-rot), math.sin(-rot)
            px, py = X[j0:j1, i0:i1], Y[j0:j1, i0:i1]
            lx = c * (px - cx) - s * (py - cy)
            ly = s * (px - cx) + c * (py - cy)
            m = (np.abs(lx) <= w / 2 + 1) & (np.abs(ly) <= d / 2 + 1)
            grid.ground[j0:j1, i0:i1][m] = G_IDX['ROAD']


def village_meta(layout, biome: str, lat: float, lon: float) -> dict:
    n = int(round(2 * WORLD_HALF / CELL)) + 1
    return dict(origin_lat=lat, origin_lon=lon, cell_m=CELL, cols=n, rows=n, x0=-WORLD_HALF, y0=-WORLD_HALF,
                water_level=layout['lake']['level'], biome=biome, source='village', detail=0, road_width=4.0, seed=layout['seed'],
                village=layout)


def road_corridors(layout) -> list:
    return [dict(id=r['name'], path=r['path'], width=r['width'], length_km=0) for r in layout['roads']]


# ── runtime geometry: collision, line of sight, bullets ─────────────────────

class Walls:
    def __init__(self, layout):
        self.w = [tuple(w) for w in layout['walls']]
        self.cells: dict = {}
        for k, (ax, ay, bx, by, *_r) in enumerate(self.w):
            for key in self._cells_for(min(ax, bx) - 0.5, min(ay, by) - 0.5, max(ax, bx) + 0.5, max(ay, by) + 0.5):
                self.cells.setdefault(key, []).append(k)
        self.buildings = layout['buildings']

    @staticmethod
    def _cells_for(x0, y0, x1, y1):
        for i in range(int(math.floor(x0 / HASH)), int(math.floor(x1 / HASH)) + 1):
            for j in range(int(math.floor(y0 / HASH)), int(math.floor(y1 / HASH)) + 1):
                yield (i, j)

    def near(self, x, y, r):
        out = set()
        for key in self._cells_for(x - r, y - r, x + r, y + r):
            out.update(self.cells.get(key, ()))
        return sorted(out)

    def along(self, ax, ay, bx, by):
        L = math.hypot(bx - ax, by - ay)
        n = max(1, int(L / (HASH * 0.5)))
        out = set()
        for k in range(n + 1):
            x, y = ax + (bx - ax) * k / n, ay + (by - ay) * k / n
            out.update(self.cells.get((int(math.floor(x / HASH)), int(math.floor(y / HASH))), ()))
        return sorted(out)

    def ray(self, ax, ay, az, bx, by, bz):
        """Fraction t ∈ [0, 1] where the 3D segment a→b first meets a wall, or None."""
        best = None
        rx, ry = bx - ax, by - ay
        for k in self.along(ax, ay, bx, by):
            qx, qy, sx, sy, zb, z0, z1, _ = self.w[k]
            t = _seg_hit(ax, ay, rx, ry, qx, qy, sx - qx, sy - qy)
            if t is None or (best is not None and t >= best):
                continue
            z = az + (bz - az) * t
            if zb + z0 <= z <= zb + z1:
                best = t
        return best

    def clear(self, ax, ay, az, bx, by, bz) -> bool:
        return self.ray(ax, ay, az, bx, by, bz) is None

    def walkable(self, ax, ay, bx, by, r=BODY_R + 0.1) -> bool:
        """No movement-blocking wall within r of the segment."""
        L = math.hypot(bx - ax, by - ay) or 1e-6
        nx, ny = -(by - ay) / L * r, (bx - ax) / L * r
        for k in self.along(ax - nx, ay - ny, bx + nx, by + ny) if L < 2 * HASH else self._along_wide(ax, ay, bx, by, r):
            qx, qy, sx, sy, zb, z0, z1, _ = self.w[k]
            if z0 >= BLOCK_Z:
                continue
            for ox, oy in ((0, 0), (nx, ny), (-nx, -ny)):
                if _seg_hit(ax + ox, ay + oy, bx - ax, by - ay, qx, qy, sx - qx, sy - qy) is not None:
                    return False
            if _seg_dist(qx, qy, ax, ay, bx, by)[0] < r or _seg_dist(sx, sy, ax, ay, bx, by)[0] < r:
                return False
        return True

    def _along_wide(self, ax, ay, bx, by, r):
        out = set()
        L = math.hypot(bx - ax, by - ay)
        n = max(1, int(L / (HASH * 0.5)))
        for k in range(n + 1):
            x, y = ax + (bx - ax) * k / n, ay + (by - ay) * k / n
            out.update(self.near(x, y, r + 0.5))
        return sorted(out)

    def collide(self, x0, y0, x1, y1, r=BODY_R):
        """Slide a circle from (x0, y0) toward (x1, y1) without passing through walls."""
        dist = math.hypot(x1 - x0, y1 - y0)
        n = max(1, int(math.ceil(dist / 0.25)))
        x, y = x0, y0
        lim = r + WALL_T / 2
        for s in range(1, n + 1):
            x += (x1 - x0) / n
            y += (y1 - y0) / n
            for _ in range(2):
                for k in self.near(x, y, lim + 0.5):
                    ax, ay, bx, by, zb, z0, z1, _k = self.w[k]
                    if z0 >= BLOCK_Z:
                        continue
                    d, cx, cy = _seg_dist(x, y, ax, ay, bx, by)
                    if d < lim:
                        if d < 1e-6:
                            L = math.hypot(bx - ax, by - ay) or 1
                            ux, uy = -(by - ay) / L, (bx - ax) / L
                        else:
                            ux, uy = (x - cx) / d, (y - cy) / d
                        x, y = cx + ux * lim, cy + uy * lim
        return x, y

    def overlaps(self, x, y, r) -> bool:
        lim = r + WALL_T / 2
        for k in self.near(x, y, lim + 0.5):
            ax, ay, bx, by, zb, z0, z1, _ = self.w[k]
            if z0 < BLOCK_Z and _seg_dist(x, y, ax, ay, bx, by)[0] < lim:
                return True
        return False

    def vehicle_blocked(self, x, y, heading, length, width) -> bool:
        h = math.radians(heading)
        fx, fy = math.sin(h), math.cos(h)
        n = max(1, int(math.ceil(length / width)))
        for k in range(n):
            o = -length / 2 + width / 2 + (length - width) * (k / max(1, n - 1) if n > 1 else 0.5)
            if self.overlaps(x + fx * o, y + fy * o, width / 2):
                return True
        return False

    def indoors(self, x, y):
        for b in self.buildings:
            if inside_rect(x, y, b):
                return b
        return None


# ── bots' walking graph ─────────────────────────────────────────────────────

class NavGraph:
    """Waypoints (open-ground grid, doors inside/outside, rooms, gates) joined where a soldier can walk."""

    def __init__(self, layout, walls: Walls, deep_water):
        self.walls = walls
        pts = []
        half = layout['play_half'] - 6
        lake = layout['lake']
        blocked = lambda x, y: (lake_rel(lake, x, y) < 1.08 or walls.indoors(x, y) is not None
                                or walls.overlaps(x, y, BODY_R + 0.4) or deep_water(x, y))
        g = 16.0
        v = -half
        while v <= half + 1e-6:
            u = -half
            while u <= half + 1e-6:
                if not blocked(u, v):
                    pts.append((u, v))
                u += g
            v += g
        for b in layout['buildings']:
            for (dx, dy, nx, ny) in b['doors']:
                pts.append((dx + nx * 1.1, dy + ny * 1.1))
                pts.append((dx - nx * 1.1, dy - ny * 1.1))
            pts += [tuple(r) for r in b['rooms']]
            cp = b.get('compound')
            if cp:
                gx, gy = cp['gate']
                nx, ny = cp['gate_n']
                pts += [(gx + nx * 2, gy + ny * 2), (gx - nx * 2, gy - ny * 2)]
                bx, by = cp['back_gate']
                pts += [(bx - nx * 1.5, by - ny * 1.5), (bx + nx * 1.5, by + ny * 1.5)]
        for s in layout['spawns'].values():
            pts.append(tuple(s[0]))
        self.pts = [p for p in pts if not walls.overlaps(p[0], p[1], BODY_R + 0.05)]
        self.adj = [[] for _ in self.pts]
        self.bucket: dict = {}
        B = 24.0
        for k, (x, y) in enumerate(self.pts):
            self.bucket.setdefault((int(x // B), int(y // B)), []).append(k)
        self.B = B
        for k, (x, y) in enumerate(self.pts):
            for m in self._around(x, y, 23.0):
                if m <= k:
                    continue
                px, py = self.pts[m]
                d = math.hypot(px - x, py - y)
                if d > 23.0 or d < 0.3:
                    continue
                if self._edge_ok(x, y, px, py, lake, deep_water):
                    self.adj[k].append((m, d))
                    self.adj[m].append((k, d))

    def _around(self, x, y, r):
        B = self.B
        out = []
        for i in range(int((x - r) // B), int((x + r) // B) + 1):
            for j in range(int((y - r) // B), int((y + r) // B) + 1):
                out += self.bucket.get((i, j), [])
        return out

    def _edge_ok(self, ax, ay, bx, by, lake, deep_water):
        d = math.hypot(bx - ax, by - ay)
        for k in range(1, int(d / 4) + 1):
            t = k * 4 / d
            x, y = ax + (bx - ax) * t, ay + (by - ay) * t
            if lake_rel(lake, x, y) < 1.02 or deep_water(x, y):
                return False
        return self.walls.walkable(ax, ay, bx, by)

    def nearest(self, x, y, k=6, need_clear=True):
        cands = sorted(self._around(x, y, 30.0), key=lambda m: math.hypot(self.pts[m][0] - x, self.pts[m][1] - y))
        out = []
        for m in cands[:24]:
            px, py = self.pts[m]
            if not need_clear or self.walls.walkable(x, y, px, py, BODY_R):
                out.append(m)
                if len(out) >= k:
                    break
        return out

    def path(self, a, b):
        """List of waypoints from a to b (excluding a), or None."""
        if self.walls.walkable(a[0], a[1], b[0], b[1], BODY_R) and math.hypot(b[0] - a[0], b[1] - a[1]) < 30:
            return [tuple(b)]
        starts = self.nearest(*a)
        goals = set(self.nearest(*b))
        if not starts or not goals:
            return None
        gx, gy = b
        openq = []
        g = {}
        came = {}
        for s in starts:
            d = math.hypot(self.pts[s][0] - a[0], self.pts[s][1] - a[1])
            g[s] = d
            heapq.heappush(openq, (d + math.hypot(self.pts[s][0] - gx, self.pts[s][1] - gy), s))
            came[s] = None
        seen = 0
        while openq and seen < 4000:
            _, n = heapq.heappop(openq)
            seen += 1
            if n in goals:
                out = [tuple(b)]
                while n is not None:
                    out.append(self.pts[n])
                    n = came[n]
                return out[::-1]
            for m, d in self.adj[n]:
                ng = g[n] + d
                if ng < g.get(m, 1e18):
                    g[m] = ng
                    came[m] = n
                    heapq.heappush(openq, (ng + math.hypot(self.pts[m][0] - gx, self.pts[m][1] - gy), m))
        return None
