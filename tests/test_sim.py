"""Engine unit tests — pure Python, no Django (SKILL.md invariant 8). Run: python -m pytest tests"""
import math

import numpy as np
import pytest

from sim.engine import Battle, filter_frame
from sim.geo import curvature_drop, point_in_polygon, to_geo, to_local
from sim.inventory import catalogue, default_order_of_battle
from sim.pathfind import NavGrid, generate_corridors
from sim.physics import (effective_armour, hit_probability, impact_facet, pen_at_range, step_mobility,
                         terrain_speed_cap)
from sim.terrain import TerrainGrid
from sim.theatres import THEATRES, default_zones, theatre_by_id

CAT = catalogue()
P = CAT['platforms']
W = CAT['weapons']


def grid(elev_fn=None, n=161, cell=50.0, water=None, biome='DESERT'):
    half = (n - 1) * cell / 2
    meta = dict(origin_lat=27.58, origin_lon=70.0, cell_m=cell, cols=n, rows=n, x0=-half, y0=-half,
                water_level=water, biome=biome, source='terrarium')
    jj, ii = np.mgrid[0:n, 0:n]
    x, y = -half + ii * cell, -half + jj * cell
    e = elev_fn(x, y) if elev_fn else np.full((n, n), 100.0)
    return TerrainGrid(meta, np.asarray(e, dtype=np.float32))


# ── geometry ────────────────────────────────────────────────────────────────

def test_local_geo_roundtrip():
    o = (27.58, 70.0)
    lon, lat = to_geo(o, 12345.0, -6789.0)
    x, y = to_local(o, lon, lat)
    assert abs(x - 12345) < 0.01 and abs(y + 6789) < 0.01


def test_curvature_drop_matches_refraction_formula():
    # 10 km: d²/(2·k·R) ≈ 5.9 m with k = 4/3
    assert 5.5 < curvature_drop(10_000) < 6.3


def test_point_in_polygon():
    sq = [[0, 0], [10, 0], [10, 10], [0, 10]]
    assert point_in_polygon(5, 5, sq) and not point_in_polygon(15, 5, sq)


# ── terrain & LOS (TR-3, TR-7) ─────────────────────────────────────────────

def test_terrain_classes_and_slope():
    g = grid(lambda x, y: 100 + 0.5 * np.maximum(x, 0))  # 26.6° ramp east of x=0
    assert g.class_at(-2000, 0) == 'FLAT'
    assert g.class_at(2000, 0) == 'STEEP'
    assert abs(g.slope_at(2000, 0) - math.degrees(math.atan(0.5))) < 1
    assert g.grade_along(2000, 0, 90) > 40 and g.grade_along(2000, 0, 270) < -40


def test_los_blocked_by_ridge_and_clear_over_flat():
    ridge = grid(lambda x, y: np.where(np.abs(x) < 200, 160.0, 100.0))
    assert not ridge.los(-2000, 0, 103, 2000, 0, 103)
    assert ridge.los(-2000, 0, 103, -1000, 0, 103)


def test_los_earth_curvature_hides_low_targets_at_long_range():
    g = grid(n=801, cell=50.0)  # 40 km flat
    # eye 3 m, target 2 m at 18 km: horizon from 3 m ≈ 7.1 km → hidden behind the earth bulge
    assert not g.los(-9000, 0, 103, 9000, 0, 102)
    assert g.los(-2000, 0, 103, 2000, 0, 102)


def test_water_classification():
    g = grid(lambda x, y: np.where(x > 0, 1.0, 10.0), water=4.0, biome='MARSH')
    assert g.ground_at(2000, 0) == 'WATER' and g.water_depth(2000, 0) > 2
    assert g.ground_at(-2000, 0) != 'WATER'


# ── mobility physics (PW-5, TR-4) ───────────────────────────────────────────

def drive(spec, grade, ground='GRASS', water=0.0, secs=60, corridor=False):
    v = 0.0
    for _ in range(int(secs / 0.25)):
        r = step_mobility(spec, v, 1.0, grade, ground, water, corridor, 1.0, 200, 0.25, False, True)
        v = r['speed']
    return v, r


def test_uphill_slower_than_flat_and_downhill():
    mbt = P['b-mbt-vajra']
    flat, _ = drive(mbt, 0)
    up, _ = drive(mbt, 35)
    down, _ = drive(mbt, -20)
    assert up < flat * 0.8
    assert down >= flat * 0.99


def test_gradient_limit_blocks():
    mbt = P['b-mbt-vajra']
    v, r = drive(mbt, 100)  # 45°, beyond the 32° limit
    assert r['blocked'] and v <= 0.5


def test_mud_slower_than_road():
    apc = P['b-apc-kavach']
    road, _ = drive(apc, 0, 'ROAD', corridor=True)
    mud, _ = drive(apc, 0, 'MUD')
    assert mud < road * 0.6


def test_water_swim_vs_blocked():
    _, swim, blocked = terrain_speed_cap(P['b-ifv-garuda'], 'WATER', False, 3.0)
    assert swim and not blocked
    cap, _, blocked = terrain_speed_cap(P['b-mbt-vajra'], 'WATER', False, 3.0)
    assert blocked and cap == 0


def test_thin_air_reduces_climb_speed():
    mbt = P['b-mbt-bhishma']
    low = step_mobility(mbt, 3.0, 1.0, 30, 'ROCK', 0, False, 1.0, 200, 1.0, False, True)['speed']
    high = step_mobility(mbt, 3.0, 1.0, 30, 'ROCK', 0, False, 1.0, 4500, 1.0, False, True)['speed']
    assert high < low


# ── gunnery & armour (PW-6, PW-7) ──────────────────────────────────────────

def hp(w, rng_m, conceal=0.0, moving=0.0):
    return hit_probability(w, rng_m, moving, True, 0.8, 90, 0.0, 9.8, 2.5, conceal, 1.0, 1.0, True)


def test_hit_probability_falls_with_range_and_concealment():
    w = W['apfsds-125']
    assert hp(w, 1000) > hp(w, 3000) > 0
    assert hp(w, 2000, conceal=0.6) < hp(w, 2000)
    assert hp(w, 9000) == 0  # beyond max range


def test_facets_and_obliquity():
    assert impact_facet(0, 0, False)[0] == 'front'
    assert impact_facet(0, 90, False)[0] == 'side'
    assert impact_facet(0, 180, False)[0] == 'rear'
    assert impact_facet(0, 0, True)[0] == 'top'
    a = P['b-mbt-vajra']['armour']
    assert effective_armour(a, 'front', 60) > a['front'] * 1.9


def test_ke_penetration_drops_with_range_ce_does_not():
    assert pen_at_range(W['apfsds-125'], 4000) < pen_at_range(W['apfsds-125'], 500)
    assert pen_at_range(W['heat-125'], 4000) == pen_at_range(W['heat-125'], 500)


# ── battle engine (SC-4, SC-5, LS-7, AI) ───────────────────────────────────

def make_battle(seed=7, flat=True, ticks=2000, theatre='thar-longewala'):
    t = theatre_by_id(theatre)
    g = grid(n=721, cell=50.0)  # 36 km flat AO
    zones = default_zones(t, 18)
    cor = generate_corridors(g, zones)
    g.set_corridors([c['path'] for c in cor], 70)
    b = Battle('t', dict(theatre=t.id, tick_s=0.5, max_ticks=ticks, weather='CLEAR', time_of_day='DAY'), g, cor, zones,
               catalogue(), seed)
    b.deploy('BLUE', b.auto_plan('BLUE'))
    b.deploy('RED', b.auto_plan('RED', weighted=True))
    return b


def disarm(b, side):
    for u in b.units.values():
        if u.side == side:
            u.ammo = {}
            u.reload = {}


def test_deployment_inside_zones_and_hvts_exist():
    b = make_battle()
    for side in ('BLUE', 'RED'):
        assert b.hvt(side) is not None
        inside = [u for u in b.units.values() if u.side == side and point_in_polygon(u.x, u.y, b.zones[side])]
        assert len(inside) >= 0.8 * sum(1 for u in b.units.values() if u.side == side)


def test_red_units_get_weighted_doctrines():
    b = make_battle()
    docs = {u.doctrine for u in b.units.values() if u.side == 'RED' and not u.is_hvt}
    assert len(docs) >= 2  # RP-1: each unit gets its own weighted doctrine


@pytest.mark.parametrize('red_dead,blue_dead,kb,kr,expected', [
    (True, False, 0, 9, 'BLUE'),       # enemy HVT decides outright, even when behind on kills
    (False, True, 9, 0, 'RED'),
    (True, True, 3, 3, 'STALEMATE'),
    (False, False, 5, 3, 'BLUE'),
    (False, False, 2, 4, 'RED'),
    (False, False, 4, 4, 'DRAW'),
])
def test_winner_rules(red_dead, blue_dead, kb, kr, expected):
    b = make_battle()
    b.start()
    if red_dead:
        b.hvt('RED').status = 'DESTROYED'
    if blue_dead:
        b.hvt('BLUE').status = 'DESTROYED'
    b.kills = {'BLUE': kb, 'RED': kr}
    b.finish('test')
    assert b.outcome == expected


def test_battle_runs_and_ends_with_time_limit_or_decision():
    b = make_battle(ticks=600)
    b.start()
    while b.status == 'RUNNING':
        b.step()
    assert b.status == 'ENDED' and b.outcome in ('BLUE', 'RED', 'DRAW', 'STALEMATE')
    assert b.tick <= 600


def test_same_seed_same_battle_across_processes():
    import subprocess
    import sys
    code = ("import sys; sys.path[:0]=['tests','.']; from test_sim import make_battle; b=make_battle(seed=5, ticks=200); b.start(); "
            "[b.step() for _ in range(200)]; print(round(sum(u.x+u.y+u.hp for u in b.units.values()), 3))")
    outs = {subprocess.run([sys.executable, '-c', code], env={'PYTHONHASHSEED': str(h), 'PATH': ''}, capture_output=True, text=True).stdout
            for h in (1, 2)}
    assert len(outs) == 1 and outs.pop().strip()


def test_same_seed_same_battle():
    def run(seed):
        b = make_battle(seed=seed, ticks=300)
        b.start()
        for _ in range(300):
            b.step()
        return [(u.id, round(u.x, 3), round(u.y, 3), round(u.hp, 3)) for u in b.units.values()]
    assert run(11) == run(11)


def test_fog_of_war_filters_undetected_enemies():
    b = make_battle()
    b.start()
    b.step()
    fr = b.frame()
    blue_view = filter_frame(fr, 'BLUE', True)
    seen = set(fr['detected']['BLUE'])
    for u in blue_view['units']:
        assert u['s'] == 'BLUE' or u['id'] in seen or (u['hvt'] and fr['hvt_acquired']['BLUE'])
    admin = filter_frame(fr, 'ADMIN', True)
    assert len(admin['units']) == len(fr['units'])
    # with fog disabled players see everything
    assert len(filter_frame(fr, 'BLUE', False)['units']) == len(fr['units'])


def test_enemy_hvt_hidden_until_acquired():
    b = make_battle()
    b.start()
    b.step()
    fr = b.frame()
    fr['hvt_acquired']['BLUE'] = False
    fr['detected']['BLUE'] = []
    ids = {u['id'] for u in filter_frame(fr, 'BLUE', True)['units']}
    assert 'R-HVT' not in ids
    fr['hvt_acquired']['BLUE'] = True
    assert 'R-HVT' in {u['id'] for u in filter_frame(fr, 'BLUE', True)['units']}


def test_route_command_moves_formation():
    b = make_battle()
    disarm(b, 'RED')  # movement only — no enemy fire
    b.start()
    f = b.formations[('BLUE', 'ALPHA')]
    lead = b.units[f.unit_ids[0]]
    x0, y0 = lead.x, lead.y
    b.submit('BLUE', 'p1', {'type': 'route', 'formation': 'ALPHA', 'waypoints': [[x0 + 3000, y0]]})
    b.formations[('BLUE', 'ALPHA')].controller = 'p1'
    for _ in range(120):
        b.step()
    assert math.hypot(lead.x - x0, lead.y - y0) > 400


def test_direct_control_drives_vehicle():
    b = make_battle()
    disarm(b, 'RED')
    b.start()
    u = b.units['B-MBT-1']
    b.submit('BLUE', 'p1', {'type': 'possess', 'unit_id': u.id})
    b.step()
    assert u.controller == 'p1'
    x0, y0 = u.x, u.y
    for _ in range(60):
        b.set_drive(u.id, {'throttle': 1.0, 'steer': 0.0, 'turret_yaw': u.heading, 'gun_pitch': 0})
        b.step()
    assert u.speed > 5 and math.hypot(u.x - x0, u.y - y0) > 100


def test_corridors_generated_between_zones():
    b = make_battle()
    assert 1 <= len(b.corridors) <= 3
    assert all(len(c['path']) >= 2 for c in b.corridors)


def test_nav_avoids_steep_wall():
    wall = grid(lambda x, y: np.where((np.abs(x) < 300) & (y > -2000), 400.0, 100.0))
    nav = NavGrid(wall)
    path = nav.find_path((-2000, 2000), (2000, 2000), 'TRACKED')
    assert path is not None
    assert min(p[1] for p in path) < -1800  # went around the south end of the wall


def test_all_theatres_have_default_zones_on_opposite_sides():
    for t in THEATRES:
        z = default_zones(t, 18)
        bx = sum(p[0] for p in z['BLUE']) / 4, sum(p[1] for p in z['BLUE']) / 4
        rx = sum(p[0] for p in z['RED']) / 4, sum(p[1] for p in z['RED']) / 4
        assert math.hypot(bx[0] - rx[0], bx[1] - rx[1]) > 5000


def test_default_order_of_battle_is_balanced():
    pts = {s: sum(P[p]['points'] * n for f in default_order_of_battle(s) for p, n in f['composition'].items()) for s in ('BLUE', 'RED')}
    assert pts['BLUE'] == pts['RED']


def test_player_soldier_walks_boards_and_respawns():
    b = make_battle(ticks=400)
    a = b.spawn_avatar('BLUE', 'p1', 'Viper')
    assert a.avatar and a.cls == 'INF' and a.controller == 'p1'
    b.start()
    x0, y0 = a.x, a.y
    b.set_drive(a.id, dict(throttle=1, sprint=True, turret_yaw=90))
    for _ in range(10):
        b.step()
    d = math.hypot(a.x - x0, a.y - y0)
    assert 15 < d < 35 and a.x > x0  # ~5.8 m/s east for 5 s
    # board the nearest friendly vehicle
    v = next(u for u in b.units.values() if u.side == 'BLUE' and u.alive and not u.is_hvt and not u.is_air and u.cls != 'INF')
    a.x, a.y = v.x + 2, v.y
    b.set_drive(a.id, {})
    b.submit('BLUE', 'p1', {'type': 'enter'})
    b.step()
    assert a.mounted_in == v.id and v.controller == 'p1'
    b.step()
    assert (a.x, a.y) == (v.x, v.y)
    b.submit('BLUE', 'p1', {'type': 'exit'})
    b.step()
    assert a.mounted_in is None and v.controller == 'AI' and 2 < math.hypot(a.x - v.x, a.y - v.y) < 10
    # soldiers do not decide the battle and respawn after death
    b.destroy(a, None, None, 0)
    assert not a.alive and a.respawn_t == 20
    for _ in range(41):
        b.step()
    assert a.alive and a.hp == a.max_hp


def test_called_vehicle_drives_to_the_soldier_and_waits():
    b = make_battle(ticks=2000)
    a = b.spawn_avatar('BLUE', 'p1', 'Viper')
    disarm(b, 'RED')  # nobody shoots the waiting soldier
    b.start()
    b.submit('BLUE', 'p1', {'type': 'summon'})
    b.step()
    v = next(u for u in b.units.values() if u.pickup == 'p1')
    d0 = math.hypot(v.x - a.x, v.y - a.y)
    for _ in range(int(min(600, d0 / 4 + 60) / 0.5)):
        b.step()
        if math.hypot(v.x - a.x, v.y - a.y) < 7 and abs(v.speed) < 0.5:
            break
    assert math.hypot(v.x - a.x, v.y - a.y) < 8, (d0, math.hypot(v.x - a.x, v.y - a.y))
    b.submit('BLUE', 'p1', {'type': 'enter'})
    b.step()
    assert a.mounted_in == v.id and v.pickup is None


# ── Village Skirmish ─────────────────────────────────────────────────────────

@pytest.fixture(scope='module')
def village_map():
    from sim import village as V
    lay = V.generate(1234)
    g = TerrainGrid(V.village_meta(lay, 'MONSOON', 30.9, 74.6), V.terrain_elevation(lay))
    g.set_corridors([r['path'] for r in lay['roads']], 4.0)
    return lay, g


def make_skirmish(village_map, seed=5):
    from sim.skirmish import Skirmish
    lay, g = village_map
    return Skirmish('s', dict(theatre='punjab-asal-uttar', tick_s=0.1, max_ticks=6000, weather='CLEAR', time_of_day='DAY'),
                    g, [], {}, catalogue(), seed)


def test_squad_size_and_red_hunts_players(village_map):
    from sim.skirmish import Skirmish
    lay, g = village_map
    b = Skirmish('s', dict(theatre='punjab-asal-uttar', tick_s=0.1, max_ticks=6000, weather='CLEAR', time_of_day='DAY', squad=3),
                 g, [], {}, catalogue(), 5)
    b.start()
    assert b.squad_alive() == {'BLUE': 3, 'RED': 3}
    # a RED bot in a fight calls the contact out: an idle squad mate nearby comes to search there
    bots = [u for u in b.units.values() if u.side == 'RED' and u.bot]
    st = b.agents['RED'].st
    a, m = st[bots[0].id], st[bots[1].id]
    a.mode, a.last_seen = 'ENGAGE', (bots[0].x + 40, bots[0].y, b.sim_time)
    m.mode = 'PATROL'
    b.agents['RED']._callouts()
    assert m.mode == 'SEARCH' and m.last_seen == a.last_seen


def test_village_layout_is_playable(village_map):
    from sim import village as V
    from sim.theatres import G_IDX
    lay, g = village_map
    kinds = {b['k'] for b in lay['buildings']}
    assert len(lay['buildings']) > 60 and {'hut', 'house', 'big', 'school'} <= kinds
    assert {v['k'] for v in lay['vehicles']} >= {'car', 'bike', 'jeep', 'tank'} and len(lay['loot']) > 80
    assert V.generate(1234)['walls'] == lay['walls']  # deterministic
    # water only in the lake, never under a house
    jj, ii = np.nonzero(g.ground == G_IDX['WATER'])
    assert all(V.lake_rel(lay['lake'], g.x0 + i * g.cell, g.y0 + j * g.cell) < 1.1 for j, i in zip(jj[::25], ii[::25]))
    assert all(b['zb'] > lay['lake']['level'] for b in lay['buildings'])
    # bots can walk almost everywhere (doors, rooms, open ground form one graph)
    from sim.skirmish import village_geometry
    walls, graph = village_geometry(g)
    seen, stack = {0}, [0]
    while stack:
        n = stack.pop()
        for m, _ in graph.adj[n]:
            if m not in seen:
                seen.add(m)
                stack.append(m)
    assert len(seen) > 0.95 * len(graph.pts)
    room = lay['buildings'][0]['rooms'][0]
    assert graph.path(tuple(lay['spawns']['BLUE'][0]), tuple(room)) is not None


def test_walls_stop_people_and_bullets(village_map):
    b = make_skirmish(village_map)
    hut = next(x for x in b.layout['buildings'] if x['k'] in ('hut', 'house'))
    a = b.spawn_avatar('BLUE', 'p1', 'Viper')
    # stand 3 m in front of the front wall, then walk straight through it: the wall stops you
    import math as m
    fx, fy = -m.sin(hut['rot']), m.cos(hut['rot'])  # local +y (back) direction
    ox, oy = hut['cx'] - fx * (hut['d'] / 2 + 3), hut['cy'] - fy * (hut['d'] / 2 + 3)
    side = (m.cos(hut['rot']) * hut['w'] * 0.4, m.sin(hut['rot']) * hut['w'] * 0.4)  # beside the door
    if hut['k'] == 'hut':
        side = (m.cos(hut['rot']) * 2.3, m.sin(hut['rot']) * 2.3)
    a.x, a.y = ox + side[0], oy + side[1]
    b.start()
    for u in b.units.values():  # bots out of the way
        if u.bot:
            u.status = 'DESTROYED'
    tx, ty = a.x + fx * 6, a.y + fy * 6
    for _ in range(40):
        b.set_drive(a.id, dict(throttle=1, turret_yaw=m.degrees(m.atan2(fx, fy)) % 360))
        b.step()
    assert not b.walls.indoors(a.x, a.y), 'walked through a wall'
    # an enemy inside the house cannot be shot from outside through the wall
    e = b.spawn_bot('RED')
    e.x, e.y = hut['rooms'][0]
    b._place(e)
    e.z = b.ground_z(e.x, e.y)
    hp = e.hp
    from sim import combat
    a.ammo['pistol-p9'] = 100
    a.mag['pistol-p9'] = 15
    for _ in range(10):
        a.reload['pistol-p9'] = 0
        combat.manual_fire(b, a, dict(weapon=0, aim=[e.x, e.y, e.z + 1.2]))
    assert e.hp == hp and not b.sees(a, e)


def test_loot_pickup_magazines_and_reload(village_map):
    b = make_skirmish(village_map)
    a = b.spawn_avatar('BLUE', 'p1', 'Viper')
    b.start()
    it = next(i for i in b.items.values() if i['k'] == 'rifle-m4')
    a.x, a.y = it['x'] + 0.5, it['y']
    b._place(a)
    a.z = b.ground_z(a.x, a.y)
    b.submit('BLUE', 'p1', {'type': 'pick', 'item': it['id']})
    b.step()
    assert 'rifle-m4' in a.loadout and a.mag['rifle-m4'] == 30 and it['id'] not in b.items
    a.mag['rifle-m4'] = 0
    b.submit('BLUE', 'p1', {'type': 'reload'})
    for _ in range(30):
        b.step()
    assert a.mag['rifle-m4'] == 30 and a.ammo['rifle-m4'] == 30


def test_anyone_can_drive_a_parked_vehicle(village_map):
    b = make_skirmish(village_map)
    a = b.spawn_avatar('RED', 'p2', 'Raven')
    b.start()
    car = next(u for u in b.units.values() if u.platform_id == 'v-car')
    assert car.side == 'NEUT' and car.controller == 'PARKED'
    a.x, a.y = car.x + 2.5, car.y
    b.submit('RED', 'p2', {'type': 'enter'})
    b.step()
    assert a.mounted_in == car.id and car.side == 'RED' and car.controller == 'p2'
    b.submit('RED', 'p2', {'type': 'exit'})
    b.step()
    assert a.mounted_in is None and car.side == 'NEUT' and car.controller == 'PARKED'


def test_bot_match_finishes_with_a_winner(village_map):
    b = make_skirmish(village_map, seed=9)
    b.start()
    assert b.squad_alive() == {'BLUE': 10, 'RED': 10}
    assert len({u.name for u in b.units.values() if u.cls == 'INF'}) == 20
    while b.status == 'RUNNING':
        b.step()
    assert b.outcome in ('BLUE', 'RED', 'DRAW') and not b.bot_errors
    res = b.result()
    assert res['mode'] == 'SKIRMISH' and sum(res['kills'].values()) >= 1
