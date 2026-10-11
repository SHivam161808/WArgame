"""Tick-based battle engine (SC-2..5, LS-1). Pure Python — no Django imports.

Step pipeline (SKILL.md invariant 4):
commands → AI decisions → movement → detection → targeting & fire → projectiles →
damage & morale → end conditions → record frame.
"""
from __future__ import annotations

import math
import random
import time
import zlib
from collections import deque

from . import combat
from .ai_agent import AIAgent
from .formations import formation_offsets, offset_to_world
from .geo import angle_diff, bearing, clamp, point_in_polygon, polygon_centroid, random_point_in_polygon
from .inventory import FORMATION_IDS, FORMATION_ROLE, default_order_of_battle, hvt_platform_for
from .pathfind import NavGrid, nav_profile
from .physics import GROUND, fuel_burn, step_mobility, turn_rate
from .theatres import WEATHER, battle_hour, light_at_hour, theatre_by_id
from .units import Formation, Projectile, Unit

SIDES = ('BLUE', 'RED')
CLS_CODE = {'MBT': 'MBT', 'IFV': 'IFV', 'APC': 'APC', 'SPG': 'SPG', 'RECON': 'RCN', 'HOVER': 'HOV', 'GUNSHIP': 'GS',
            'JET': 'JET', 'UAV': 'UAV', 'AD': 'AD', 'MLRS': 'MLRS', 'HVT': 'HVT'}
# Weighted doctrines for AI Red per formation role (RP-1).
RED_DOCTRINE_WEIGHTS = {
    'ALPHA': {'AGGRESSIVE': 0.55, 'FLANKING': 0.35, 'SUPPRESSIVE': 0.1},
    'BRAVO': {'DEFENSIVE': 0.7, 'SUPPRESSIVE': 0.2, 'FLANKING': 0.1},
    'CHARLIE': {'SUPPRESSIVE': 0.85, 'DEFENSIVE': 0.15},
    'DELTA': {'FLANKING': 0.45, 'AGGRESSIVE': 0.3, 'DEFENSIVE': 0.25},
}


def enemy_of(side: str) -> str:
    return 'RED' if side == 'BLUE' else 'BLUE'


class Battle:
    mode = 'ARMOUR'
    def __init__(self, battle_id: str, cfg: dict, terrain, corridors: list, zones: dict, catalogue: dict, seed: int,
                 nav: NavGrid | None = None):
        self.id = battle_id
        self.cfg = cfg
        self.terrain = terrain
        self.nav = nav or NavGrid(terrain)
        self.corridors = corridors
        self.zones = zones  # {'BLUE': [[x,y],...], 'RED': ...}
        self.platforms = catalogue['platforms']
        self.weapons = catalogue['weapons']
        self.seed = seed
        self.rng = random.Random(seed)
        self.theatre = theatre_by_id(cfg.get('theatre', ''))
        self.weather = WEATHER.get(cfg.get('weather', 'CLEAR'), WEATHER['CLEAR'])
        self.dt = float(cfg.get('tick_s', 0.2))
        self.max_ticks = int(cfg.get('max_ticks', 9000))
        self.fog = bool(cfg.get('fog_enabled', True))
        self.tick = 0
        self.sim_time = 0.0
        self.status = 'DEPLOYMENT'
        self.outcome: str | None = None
        self.reason = ''
        self.units: dict[str, Unit] = {}
        self.formations: dict[tuple, Formation] = {}
        self.projectiles: list[Projectile] = []
        self.events: list[dict] = []
        self.casualties: list[dict] = []
        self.detected = {'BLUE': set(), 'RED': set()}
        self.last_seen = {'BLUE': {}, 'RED': {}}
        self.hvt_acquired = {'BLUE': False, 'RED': False}
        self.kills = {'BLUE': 0, 'RED': 0}
        self.intruders = {'BLUE': set(), 'RED': set()}
        self.last_intrusion_alert = {'BLUE': -1e9, 'RED': -1e9}
        self.commands: deque = deque()
        self.drive: dict[str, dict] = {}
        self.agents = {s: AIAgent(self, s) for s in SIDES}
        self._ids = 0
        self.light = 1.0
        self.hour = battle_hour(cfg.get('time_of_day', 'DAY'), 0)
        self.step_ms = 0.0
        self.friendly_fire = True

    # ── helpers ─────────────────────────────────────────────────────────────
    def next_id(self, prefix: str) -> str:
        self._ids += 1
        return f'{prefix}{self._ids}'

    def emit(self, ev: dict):
        self.events.append(ev)

    def new_projectile(self, **kw) -> Projectile:
        p = Projectile(**kw)
        self.projectiles.append(p)
        return p

    def alive_units(self, side: str | None = None, enemy: str | None = None):
        want = side or (enemy_of(enemy) if enemy else None)
        return [u for u in self.units.values() if u.alive and (want is None or u.side == want)]

    def hvt(self, side: str) -> Unit | None:
        for u in self.units.values():
            if u.is_hvt and u.side == side:
                return u
        return None

    def zone_centroid(self, side: str):
        z = self.zones.get(side)
        return polygon_centroid(z) if z else (0.0, 0.0)

    # ── map geometry hooks (buildings in Village Skirmish; open country otherwise) ──
    def wall_ray(self, ox, oy, oz, dx, dy, dz, max_d):
        return None

    def wall_clear(self, ax, ay, az, bx, by, bz) -> bool:
        return True

    def collide_move(self, u: Unit, x0, y0, x1, y1):
        return x1, y1

    def ground_z(self, x, y) -> float:
        return self.terrain.surface(x, y)

    def vehicle_blocked(self, u: Unit, x, y) -> bool:
        return False

    def after_move(self):
        return None

    def has_los(self, a: Unit, b: Unit) -> bool:
        return self.terrain.los(a.x, a.y, a.eye_z(), b.x, b.y, b.centre_z())

    def has_los_from_side(self, side: str, t: Unit) -> bool:
        return t.id in self.detected[side]

    # ── deployment ──────────────────────────────────────────────────────────
    def deploy(self, side: str, plan: dict):
        """Create a side's units from a deployment plan {formations: [...], hvt: [x,y]} (BP-1, BP-2)."""
        for uid in [u.id for u in self.units.values() if u.side == side]:
            del self.units[uid]
        for key in [k for k in self.formations if k[0] == side]:
            del self.formations[key]
        zone = self.zones.get(side) or []
        enemy_c = self.zone_centroid(enemy_of(side))
        counters: dict = {}
        prefix = 'B' if side == 'BLUE' else 'R'
        for fo in plan.get('formations', []):
            fid = fo['id']
            anchor = fo.get('anchor') or (random_point_in_polygon(self.rng, zone) if zone else (0, 0))
            facing = fo.get('facing')
            if facing is None:
                facing = bearing(anchor[0], anchor[1], *enemy_c)
            f = Formation(id=fid, side=side, name=fo.get('name') or f'{fid} ({FORMATION_ROLE[fid]})',
                          doctrine=fo.get('doctrine', 'AGGRESSIVE'), shape=fo.get('shape', 'WEDGE'),
                          spacing=float(fo.get('spacing', 140)), heading=facing)
            f.avenue = int(fo.get('avenue', FORMATION_IDS.index(fid) % max(1, len(self.corridors))))
            pids = [pid for pid, n in fo.get('composition', {}).items() for _ in range(int(n)) if pid in self.platforms]
            # ground units first so the leader is a ground vehicle
            pids.sort(key=lambda p: 1 if self.platforms[p].get('air') else 0)
            offs = formation_offsets(f.shape, len(pids), f.spacing)
            for slot, pid in enumerate(pids):
                spec = self.platforms[pid]
                code = CLS_CODE.get(spec['cls'], spec['cls'])
                counters[code] = counters.get(code, 0) + 1
                uid = f'{prefix}-{code}-{counters[code]}'
                ox, oy = offset_to_world(offs[slot], facing)
                x, y = self._snap(spec, anchor[0] + ox, anchor[1] + oy, zone)
                doctrine = f.doctrine
                if side == 'RED' and plan.get('weighted_doctrines'):
                    doctrine = self._weighted(RED_DOCTRINE_WEIGHTS[fid])
                u = Unit(uid, side, fid, spec, self.weapons, x, y, facing, doctrine)
                u.name = uid
                u.slot = slot
                u.avenue = f.avenue if side == 'BLUE' else self.rng.randrange(max(1, len(self.corridors)))
                u.detached = doctrine != f.doctrine
                self._place(u)
                self.units[uid] = u
                f.unit_ids.append(uid)
            self.formations[(side, fid)] = f
        hx, hy = plan.get('hvt') or (self.zone_centroid(side))
        spec = self.platforms[hvt_platform_for(side)]
        h = Unit(f'{prefix}-HVT', side, None, spec, self.weapons, hx, hy, bearing(hx, hy, *enemy_c), 'HOLD')
        h.name = spec['name']
        h.task = 'HOLD'
        self._place(h)
        self.units[h.id] = h

    def _weighted(self, weights: dict) -> str:
        r = self.rng.random() * sum(weights.values())
        for k, w in weights.items():
            r -= w
            if r <= 0:
                return k
        return next(iter(weights))

    def _snap(self, spec: dict, x: float, y: float, zone: list) -> tuple:
        x, y = self.terrain.clamp_xy(x, y)
        if spec.get('air'):
            return x, y
        prof = nav_profile(spec)
        p = self.nav.nearest_passable(x, y, prof, spec['max_grade_deg'] * 0.8)
        return (p[0], p[1]) if p else (x, y)

    def _place(self, u: Unit):
        u.home = (u.x, u.y)
        ground = self.terrain.surface(u.x, u.y)
        u.z = ground + (u.alt if u.is_air else 0)
        if not u.is_air:
            u.pitch, u.roll = self.terrain.attitude(u.x, u.y, u.heading)
        u.ground = self.terrain.ground_at(u.x, u.y)

    def auto_plan(self, side: str, inventory: dict | None = None, weighted: bool = False) -> dict:
        """Default/AI order of battle placed at random inside the zone (RP-1)."""
        zone = self.zones.get(side) or []
        oob = default_order_of_battle(side)
        if inventory:
            left = dict(inventory)
            for f in oob:
                comp = {}
                for pid, n in f['composition'].items():
                    take = min(n, left.get(pid, 0))
                    if take:
                        comp[pid] = take
                        left[pid] -= take
                f['composition'] = comp
        enemy_c = self.zone_centroid(enemy_of(side))
        own_c = self.zone_centroid(side)
        plans = []
        for f in oob:
            if zone:
                # bias: assault/recon forward, fire support rear, defence near HVT — but random inside the zone
                for _ in range(40):
                    p = random_point_in_polygon(self.rng, zone)
                    fwd = math.hypot(p[0] - enemy_c[0], p[1] - enemy_c[1]) < math.hypot(own_c[0] - enemy_c[0], own_c[1] - enemy_c[1])
                    if (f['id'] in ('ALPHA', 'DELTA')) == fwd or f['id'] == 'BRAVO':
                        break
            else:
                p = own_c
            plans.append(dict(id=f['id'], doctrine=f['doctrine'], shape=f['shape'], composition=f['composition'],
                              anchor=list(p), facing=bearing(p[0], p[1], *enemy_c)))
        hvt = None
        if zone:
            for _ in range(60):
                p = random_point_in_polygon(self.rng, zone)
                if math.hypot(p[0] - enemy_c[0], p[1] - enemy_c[1]) > math.hypot(own_c[0] - enemy_c[0], own_c[1] - enemy_c[1]):
                    hvt = list(p)
                    break
        return dict(side=side, formations=plans, hvt=hvt or list(own_c), weighted_doctrines=weighted)

    def start(self):
        self.status = 'RUNNING'
        for u in self.units.values():
            if u.is_air:
                u.orbit = self.rng.uniform(0, 360)
        self.emit({'t': 'ALERT', 'side': 'ALL', 'level': 'info', 'text': 'Battle started'})

    # ── commands (BP-3, BP-5, LS-5) ─────────────────────────────────────────
    def submit(self, side: str, player: str, cmd: dict):
        self.commands.append((side, player, cmd))

    def set_drive(self, unit_id: str, inp: dict):
        self.drive[unit_id] = inp

    def _apply_commands(self):
        while self.commands:
            side, player, c = self.commands.popleft()
            try:
                self._apply(side, player, c)
            except (KeyError, ValueError, TypeError) as e:
                self.emit({'t': 'ALERT', 'side': side, 'level': 'warn', 'text': f'order rejected: {e}'})

    def _apply(self, side: str, player: str, c: dict):
        typ = c['type']
        fid = c.get('formation')
        f = self.formations.get((side, fid)) if fid else None
        units = [self.units[i] for i in c.get('unit_ids', []) if i in self.units and self.units[i].side == side]
        lock = self.sim_time + 120
        if typ == 'route':
            wps = [self.terrain.clamp_xy(float(p[0]), float(p[1])) for p in c['waypoints']][:30]
            if units:
                for u in units:
                    u.detached = True
                    u.route = (u.route if c.get('append') else []) + [list(p) for p in wps]
                    u.path = []
                    u.task = 'MOVE'
            elif f:
                f.route = (f.route if c.get('append') else []) + [list(p) for p in wps]
                f.path = []
                f.orders_locked_until = lock
        elif typ == 'doctrine' and f:
            f.doctrine = c['doctrine']
            f.orders_locked_until = lock
            for uid in f.unit_ids:
                u = self.units[uid]
                if not u.detached:
                    u.doctrine = c['doctrine']
        elif typ == 'shape' and f:
            f.shape = c['shape']
        elif typ == 'halt':
            for u in units:
                u.route, u.path = [], []
                u.detached = True
                u.task = 'HOLD'
            if f:
                f.route, f.path = [], []
                f.orders_locked_until = lock
        elif typ == 'fireMission' and f:
            f.fire_mission = {'target': [float(c['target'][0]), float(c['target'][1])], 'rounds': int(c.get('rounds', 6))}
            f.orders_locked_until = lock
        elif typ == 'rejoin':
            for u in units:
                u.detached = False
                u.route, u.path = [], []
        elif typ == 'possess':
            uid = c.get('unit_id')
            for u in self.units.values():
                if u.controller == player and not u.avatar and not self._rider_of(u.id, player):
                    u.controller = 'AI'
                    u.task = 'IDLE'
            if uid and uid in self.units and self.units[uid].avatar:
                pass  # own soldier: always controlled by its player
            elif uid and uid in self.units and self.units[uid].side == side and self.units[uid].alive and not self.units[uid].is_hvt:
                self.units[uid].controller = player
                self.units[uid].task = 'MANUAL'
                self.drive.pop(uid, None)
        elif typ == 'enter':
            self._enter_vehicle(side, player)
        elif typ == 'exit':
            self._exit_vehicle(player)
        elif typ == 'summon':
            self._summon(side, player)
        elif typ in ('reload', 'heal', 'pick'):
            a = self.avatar_of(player)
            if a is not None and a.alive and not a.mounted_in:
                if typ == 'reload' and a.loadout:
                    combat.start_reload(self, a, a.loadout[min(a.cur_w, len(a.loadout) - 1)])
                elif typ == 'heal' and a.meds > 0 and a.heal_t <= 0 and a.hp < a.max_hp:
                    a.meds -= 1
                    a.heal_t = 4.0
                    a.mag_t = 0.0
                elif typ == 'pick':
                    self.pick_item(a, c.get('item'))

    # ── main step ───────────────────────────────────────────────────────────
    def step(self) -> dict:
        t0 = time.perf_counter()
        self.events = []
        self.casualties = []
        if self.status != 'RUNNING':
            return self.frame()
        self.tick += 1
        self.sim_time = round(self.sim_time + self.dt, 4)
        self.hour = battle_hour(self.cfg.get('time_of_day', 'DAY'), self.sim_time)
        self.light = light_at_hour(self.hour)

        self._apply_commands()
        for s in SIDES:
            self.agents[s].update()
        self._respawns()
        for u in list(self.units.values()):
            if not u.alive:
                continue
            if u.mounted_in:
                self._ride(u)
            elif u.cls == 'INF':
                self._move_infantry(u)
            else:
                self._move(u)
        self.after_move()
        if self.tick % 2 == 1 or self.dt >= 0.5:
            self._detect()
        for u in list(self.units.values()):
            if not u.alive or u.mounted_in:
                continue
            for k in u.reload:
                if u.reload[k] > 0:
                    u.reload[k] = max(0.0, u.reload[k] - self.dt)
            if u.cls == 'INF':
                combat.tick_small_arms(self, u)
            if u.controller != 'AI' and u.controller:
                inp = self.drive.get(u.id) or {}
                if inp.get('fire') or inp.get('fire_latch'):
                    combat.manual_fire(self, u, inp)
                    inp['fire_latch'] = False
            else:
                combat.auto_fire(self, u)
            combat.point_defence_vs_drones(self, u)
        for s in SIDES:
            self.agents[s].fire_support()
        combat.update_projectiles(self)
        self._morale()
        self._check_end()
        self.step_ms = (time.perf_counter() - t0) * 1000
        return self.frame()

    # ── movement (TR-4, PW-5) ───────────────────────────────────────────────
    def _target_point(self, u: Unit):
        """Where the unit wants to go this tick, and the desired speed factor."""
        if u.is_hvt:
            return None, 0
        if u.pickup:
            tp = self._pickup_target(u)
            if tp is not False:
                return tp
        if u.detached or u.formation is None:
            return self._follow_path(u, u.route, owner=u), 1.0
        f = self.formations.get((u.side, u.formation))
        if f is None:
            return None, 0
        # immobilised vehicles drop out of the formation; leadership passes to the next mobile unit
        members = [self.units[i] for i in f.unit_ids if self.units[i].alive and not self.units[i].detached
                   and not self.units[i].mobility_kill and self.units[i].fuel > 0]
        if u not in members:
            return None, 0
        ground = [m for m in members if not m.is_air]
        leader = ground[0] if ground else members[0]
        if u is leader:
            tp = self._follow_path(u, f.route, owner=f)
            return tp, self._formation_speed(ground)
        idx = members.index(u)
        offs = formation_offsets(f.shape, len(members), f.spacing)
        ox, oy = offset_to_world(offs[idx], leader.heading)
        sx, sy = leader.x + ox, leader.y + oy
        if u.is_air:
            # aircraft orbit above the formation leader / route
            return self._air_station(u, (leader.x, leader.y)), 1.0
        d = math.hypot(sx - u.x, sy - u.y)
        if d < 15 and abs(leader.speed) < 0.3:
            return None, 0
        return (sx, sy), clamp(d / 120, 0.25, 1.15)

    def _formation_speed(self, members) -> float:
        caps = [m.spec['offroad_kph'] for m in members if not m.is_air and m.spec['offroad_kph'] > 0]
        return clamp((min(caps) / max(caps)) * 0.85, 0.35, 0.9) if caps else 0.9

    def _follow_path(self, u: Unit, route: list, owner):
        """Follow waypoints, expanding each leg with A* for this platform (re-plans when blocked)."""
        if not route:
            if u.is_air:
                return self._air_station(u, (u.x, u.y))
            return None
        wp = route[0]
        if math.hypot(wp[0] - u.x, wp[1] - u.y) < (250 if u.is_air else 35):
            route.pop(0)
            u.path = []
            if not route:
                return None if not u.is_air else self._air_station(u, (u.x, u.y))
            wp = route[0]
        if u.is_air:
            return wp
        prof = nav_profile(u.spec)
        need = (not u.path or u.path_goal != tuple(wp) or u.blocked_t > 3)
        if need and self.sim_time - u.path_t > 2.0:
            u.path_t = self.sim_time
            u.blocked_t = 0
            p = self.nav.find_path((u.x, u.y), wp, prof, max_grade=u.spec['max_grade_deg'], max_nodes=40000)
            u.path = p[1:] if p else [wp]
            u.path_goal = tuple(wp)
        while u.path and math.hypot(u.path[0][0] - u.x, u.path[0][1] - u.y) < 40:
            u.path.pop(0)
        return u.path[0] if u.path else wp

    def _air_station(self, u: Unit, centre):
        """Racetrack orbit around a point for aircraft without a route."""
        r = 900 if u.spec['air'].get('hover') else 2500
        u.orbit = (u.orbit + self.dt * (u.speed / r) * 57.3) % 360 if u.speed > 1 else u.orbit
        a = math.radians(u.orbit + 25)
        return (centre[0] + math.sin(a) * r, centre[1] + math.cos(a) * r)

    def _move(self, u: Unit):
        spec = u.spec
        if u.is_hvt:
            return
        if u.is_air:
            self._move_air(u)
            return
        manual = u.controller not in ('AI', '', None)
        inp = self.drive.get(u.id) if manual else None
        traction = self.weather['traction']
        if manual:
            throttle = clamp(float((inp or {}).get('throttle', 0)), -1, 1)
            steer = clamp(float((inp or {}).get('steer', 0)), -1, 1)
            want_heading = None
            if inp and 'turret_yaw' in inp:
                d = angle_diff(u.turret_yaw, float(inp['turret_yaw']))
                step = spec.get('turret_dps', 40) * self.dt
                u.turret_yaw = (u.turret_yaw + clamp(d, -step, step)) % 360
                u.gun_pitch = clamp(float(inp.get('gun_pitch', u.gun_pitch)), -10, 25)
        else:
            tp, spd = self._target_point(u)
            if tp is None or u.task == 'HOLD' and not u.route:
                throttle, want_heading, steer = 0.0, None, 0.0
            else:
                want_heading = bearing(u.x, u.y, tp[0], tp[1])
                diff = angle_diff(u.heading, want_heading)
                dist_tp = math.hypot(tp[0] - u.x, tp[1] - u.y)
                throttle = spd * clamp(dist_tp / 60, 0.2, 1) * (0.35 if abs(diff) > 60 else 1.0)
                if u.morale < 10:
                    throttle = min(1.0, throttle * 1.2)
                steer = clamp(diff / 25, -1, 1)
        # turning
        tr = turn_rate(spec, u.ground, u.slope, u.speed, traction)
        if u.mobility_kill or u.fuel <= 0:
            steer = 0
        u.heading = (u.heading + steer * tr * self.dt) % 360
        # slope along heading — corridors are engineered roads with switchbacks (grade capped)
        on_corr = self.terrain.on_corridor(u.x, u.y)
        grade = self.terrain.grade_along(u.x, u.y, u.heading if u.speed >= 0 else (u.heading + 180) % 360)
        if on_corr:
            grade = clamp(grade, -14, 14)
        if u.speed < 0:
            grade = -grade
        depth = self.terrain.water_depth(u.x, u.y)
        ground = 'ROAD' if on_corr and depth <= 0.3 else self.terrain.ground_at(u.x, u.y)
        res = step_mobility(spec, u.speed, throttle, grade, ground, depth, on_corr, traction, u.z, self.dt,
                            u.mobility_kill, u.fuel > 0)
        # look-ahead: never drive into impassable water/cliffs
        nx = u.x + math.sin(math.radians(u.heading)) * max(0.0, res['speed']) * self.dt * 3
        ny = u.y + math.cos(math.radians(u.heading)) * max(0.0, res['speed']) * self.dt * 3
        if res['speed'] > 0 and not manual and not self.nav.passable(nx, ny, nav_profile(spec), spec['max_grade_deg'] + 4) \
                and self.nav.passable(u.x, u.y, nav_profile(spec), spec['max_grade_deg'] + 4):
            res['speed'] *= 0.3
            res['blocked'] = True
        if self.terrain.water_depth(nx, ny) > spec['ford_depth_m'] and spec['swim_kph'] <= 0:
            res['speed'] = min(res['speed'], 0.0)
            res['blocked'] = True
        u.speed = res['speed']
        u.swimming = res['swimming']
        u.cap_kph = res['cap_kph']
        u.blocked_t = u.blocked_t + self.dt if res['blocked'] and throttle > 0.05 else 0
        u.throttle = throttle
        d = u.speed * self.dt
        rad = math.radians(u.heading)
        x, y = self.terrain.clamp_xy(u.x + math.sin(rad) * d, u.y + math.cos(rad) * d)
        if self.vehicle_blocked(u, x, y):
            if abs(u.speed) > 9:  # crash
                u.hp = max(1.0, u.hp - (abs(u.speed) - 9) * 6)
                self.emit({'t': 'IMPACT', 'at': [round(x, 1), round(y, 1), round(u.z + 1, 1)], 'hit': True, 'weapon': None,
                           'target': u.id, 'dmg': 0, 'effect': 'CRASH', 'delay': 0})
            x, y = u.x, u.y
            u.speed = 0.0
        u.x, u.y = x, y
        u.fuel = max(0.0, u.fuel - fuel_burn(spec, abs(d), self.dt, grade, ground, self.weather['fuel']))
        u.z = self.terrain.surface(u.x, u.y) + (0.4 if spec.get('hover') else 0)
        u.pitch, u.roll = self.terrain.attitude(u.x, u.y, u.heading)
        if u.swimming:
            u.pitch *= 0.2
            u.roll *= 0.2
        u.slope = self.terrain.slope_at(u.x, u.y)
        u.grade = grade
        u.ground = 'WATER' if u.swimming else ground
        if not manual:
            self._separate(u)
        if u.status != 'DESTROYED':
            u.status = 'IMMOBILISED' if (u.mobility_kill or u.fuel <= 0) else ('DAMAGED' if u.hp < u.max_hp else 'OK')
        if not manual and u.task not in ('HOLD', 'MANUAL', 'INTERCEPT', 'WITHDRAW', 'FLANK', 'RTB'):
            u.task = 'ENGAGE' if u.target_id else ('MOVE' if abs(u.speed) > 0.5 else 'IDLE')

    def _separate(self, u: Unit):
        for o in self.units.values():
            if o is u or not o.alive or o.is_air or o.side != u.side or o.cls == 'INF':
                continue  # soldiers step aside for vehicles, not the other way round
            dx, dy = u.x - o.x, u.y - o.y
            d = math.hypot(dx, dy)
            if 0 < d < 14:
                push = (14 - d) * 0.25
                u.x += dx / d * push
                u.y += dy / d * push

    def _move_air(self, u: Unit):
        spec = u.spec
        air = spec['air']
        if u.servicing > 0:
            u.servicing -= self.dt
            u.speed = 0
            if u.servicing <= 0:
                u.fuel = u.fuel_max
                u.ammo = {w: self.weapons[w]['ammo'] for w in u.ammo}
                u.task = 'IDLE'
                u.alt = air['cruise_alt_m']
                self.emit({'t': 'ALERT', 'side': u.side, 'level': 'info', 'text': f'{u.name} rearmed and refuelled'})
            return
        manual = u.controller not in ('AI', '', None)
        max_v = spec['road_kph'] / 3.6
        if u.mobility_kill:
            # crippled aircraft go down
            u.alt = max(0.0, u.alt - 25 * self.dt)
            if u.alt <= 0:
                u.hp = 0
                self.destroy(u, None, None, 0)
            return
        if manual:
            inp = self.drive.get(u.id) or {}
            throttle = clamp(float(inp.get('throttle', 0.5)), 0 if not air.get('hover') else -0.3, 1)
            steer = clamp(float(inp.get('steer', 0)), -1, 1)
            u.alt = clamp(u.alt + float(inp.get('climb', 0)) * 15 * self.dt, 40, air['cruise_alt_m'] * 2)
            if 'turret_yaw' in inp:
                u.turret_yaw = float(inp['turret_yaw']) % 360
                u.gun_pitch = clamp(float(inp.get('gun_pitch', -10)), -60, 10)
            u.heading = (u.heading + steer * spec['turn_rate_dps'] * self.dt) % 360
        else:
            if u.fuel < u.fuel_max * 0.12 and u.task != 'RTB':
                u.task = 'RTB'
                u.route = [list(u.home)]
                u.detached = True
            tp, _ = self._target_point(u) if u.task != 'RTB' else (self._follow_path(u, u.route, u), 1)
            if u.task == 'RTB' and math.hypot(u.home[0] - u.x, u.home[1] - u.y) < 300:
                u.servicing = 60.0
                u.alt = 0
                u.detached = False
                u.route = []
                self.emit({'t': 'ALERT', 'side': u.side, 'level': 'info', 'text': f'{u.name} landed to rearm/refuel'})
                return
            throttle = 0.75
            if tp is not None:
                want = bearing(u.x, u.y, tp[0], tp[1])
                diff = angle_diff(u.heading, want)
                u.heading = (u.heading + clamp(diff, -spec['turn_rate_dps'] * self.dt, spec['turn_rate_dps'] * self.dt)) % 360
                if air.get('hover') and math.hypot(tp[0] - u.x, tp[1] - u.y) < 200:
                    throttle = 0.15
            if u.target_id:
                t = self.units.get(u.target_id)
                if t:
                    u.turret_yaw = bearing(u.x, u.y, t.x, t.y)
            else:
                u.turret_yaw = u.heading
            u.alt += (air['cruise_alt_m'] - u.alt) * min(1.0, self.dt * 0.5)
        target_v = max_v * throttle
        acc = 25 if air.get('hover') else 40
        u.speed += clamp(target_v - u.speed, -acc * self.dt, acc * self.dt)
        u.throttle = throttle
        d = u.speed * self.dt
        rad = math.radians(u.heading)
        u.x, u.y = self.terrain.clamp_xy(u.x + math.sin(rad) * d, u.y + math.cos(rad) * d)
        ground = self.terrain.height(u.x, u.y)
        u.z = ground + max(u.alt, 30)
        u.pitch = clamp(-u.speed / max_v * 6, -10, 10) if air.get('hover') else 0
        u.roll = 0
        u.fuel = max(0.0, u.fuel - spec['fuel_l_per_km'] * d / 1000 * self.weather['fuel'] - spec['idle_burn_lph'] * self.dt / 3600)
        if u.fuel <= 0:
            u.mobility_kill = True
        u.ground, u.slope, u.grade, u.swimming = 'AIR', 0.0, 0.0, False
        u.cap_kph = spec['road_kph']

    # ── detection & fog of war (BP-4, LS-6..8, TR-7) ────────────────────────
    def _detect(self):
        for side in SIDES:
            enemy = enemy_of(side)
            observers = [u for u in self.units.values() if u.alive and u.side == side and u.servicing <= 0 and not u.mounted_in]
            found = set()
            for e in self.units.values():
                if not e.alive or e.side != enemy or e.mounted_in:
                    continue
                if e.servicing > 0 and not e.is_air:
                    continue
                moving = abs(e.speed) > 2
                sig = e.spec['signature'] * (1.25 if moving else 1) * (1.6 if self.sim_time - e.last_fired < 4 else 1)
                if moving and not e.is_air:
                    sig *= 1 + GROUND.get(e.ground, GROUND['GRASS'])['dust'] * self.weather['dust'] * 0.35
                conceal = 0 if e.is_air else self.terrain.conceal_at(e.x, e.y) * (0.5 if moving else 1)
                for o in observers:
                    sensor = o.spec['sensor']
                    optical = self.light * self.weather['visibility']
                    vis = max(optical, self.weather['thermal'] if sensor.get('thermal') else 0)
                    rng_m = sensor['range_m']
                    if e.is_air and sensor.get('radar'):
                        vis, rng_m = max(vis, 1.0), rng_m * 1.6
                    eff = rng_m * vis * sig * (1 - conceal * 0.6)
                    dx, dy = e.x - o.x, e.y - o.y
                    if dx * dx + dy * dy > eff * eff:
                        continue
                    if self.terrain.los(o.x, o.y, o.eye_z(), e.x, e.y, e.centre_z() + (2 if e.is_air else 0)):
                        found.add(e.id)
                        break
            new = found - self.detected[side]
            self.detected[side] = found
            for eid in sorted(found):
                e = self.units[eid]
                self.last_seen[side][eid] = (e.x, e.y, e.heading, self.tick)
                if e.is_hvt and not self.hvt_acquired[side]:
                    self.hvt_acquired[side] = True
                    self.emit({'t': 'HVT_ACQUIRED', 'side': side, 'hvt': eid})
                    self.emit({'t': 'ALERT', 'side': side, 'level': 'crit', 'text': f'Enemy HVT located at grid {self.grid(e.x, e.y)}'})
            for eid in sorted(new):
                self.emit({'t': 'DETECT', 'side': side, 'target': eid})
            self._intrusion(side, found)

    def _intrusion(self, side: str, found: set):
        zone = self.zones.get(side)
        if not zone:
            return
        inside = {i for i in found if point_in_polygon(self.units[i].x, self.units[i].y, zone)}
        new = inside - self.intruders[side]
        self.intruders[side] = inside
        if new and self.sim_time - self.last_intrusion_alert[side] > 10:
            self.last_intrusion_alert[side] = self.sim_time
            comp: dict = {}
            for i in sorted(inside):
                comp[self.units[i].cls] = comp.get(self.units[i].cls, 0) + 1
            self.emit({'t': 'INTRUSION', 'side': side, 'count': len(inside), 'composition': comp})

    def grid(self, x: float, y: float) -> str:
        from .geo import grid_ref
        return grid_ref(x, y, self.terrain.half_m)

    # ── casualties, morale, end ─────────────────────────────────────────────
    def destroy(self, t: Unit, shooter: Unit | None, w: dict | None, rng_m: float):
        if t.status == 'DESTROYED':
            return
        for r in [u for u in self.units.values() if u.mounted_in == t.id]:
            self._eject(r, t, hurt=55)
        if t.avatar:
            t.respawn_t = 20.0
            t.mounted_in = None
        t.status = 'DESTROYED'
        t.hp = 0
        t.speed = 0
        t.destroyed_t = self.sim_time
        if t.controller not in ('AI', '', None):
            t.controller = 'AI'
        killer_side = shooter.side if shooter else enemy_of(t.side)
        if shooter and shooter.side != t.side:
            shooter.kills += 1
            shooter.morale = min(100, shooter.morale + 5)
        if (not shooter or shooter.side != t.side) and t.side in SIDES and killer_side in SIDES:
            self.kills[killer_side] += 1
        ev = {'t': 'KILL', 'attacker': shooter.id if shooter else None, 'victim': t.id,
              'weapon': w['id'] if w else None, 'round': w['round'] if w else None, 'range': round(rng_m)}
        self.emit(ev)
        self.casualties.append(dict(
            tick=self.tick, sim_time=self.sim_time,
            attacker_id=shooter.id if shooter else None, attacker_side=shooter.side if shooter else None,
            attacker_name=shooter.name if shooter else None, attacker_cls=shooter.cls if shooter else None,
            attacker_formation=shooter.formation if shooter else None,
            victim_id=t.id, victim_side=t.side, victim_name=t.name, victim_cls=t.cls, victim_formation=t.formation,
            weapon_id=w['id'] if w else None, weapon_name=w['name'] if w else 'crash', round=w['round'] if w else None,
            range_m=round(rng_m)))
        if t.is_hvt:
            self.emit({'t': 'ALERT', 'side': 'ALL', 'level': 'crit', 'text': f'{t.side} HVT DESTROYED'})
            for u in self.alive_units(t.side):
                u.morale = max(0, u.morale - 35)
        for u in self.alive_units(t.side):
            if math.hypot(u.x - t.x, u.y - t.y) < 1500:
                u.morale = max(0, u.morale - 10)

    def _morale(self):
        for u in self.units.values():
            if u.alive and self.sim_time - u.last_hit_t > 20:
                u.morale = min(u.morale_base, u.morale + 0.4 * self.dt)
        self._resupply()

    def _resupply(self):
        """Logistics: ground units within 1.5 km of their own HVT draw ammunition (one round per 20 s per weapon)."""
        if self.tick % max(1, int(20 / self.dt)):
            return
        for side in SIDES:
            h = self.hvt(side)
            if h is None or not h.alive:
                continue
            for u in self.alive_units(side):
                if u.is_hvt or u.is_air or math.hypot(u.x - h.x, u.y - h.y) > 1500:
                    continue
                for wid in u.ammo:
                    if u.ammo[wid] < self.weapons[wid]['ammo']:
                        u.ammo[wid] += max(1, self.weapons[wid]['salvo'])
                        u.ammo[wid] = min(u.ammo[wid], self.weapons[wid]['ammo'])

    def _check_end(self):
        bh, rh = self.hvt('BLUE'), self.hvt('RED')
        b_dead = bh is not None and not bh.alive
        r_dead = rh is not None and not rh.alive
        blue_alive = [u for u in self.alive_units('BLUE') if not u.is_hvt and not u.avatar]
        red_alive = [u for u in self.alive_units('RED') if not u.is_hvt and not u.avatar]
        reason = None
        if b_dead or r_dead:
            reason = 'HVT destroyed'
        elif not blue_alive:
            reason = 'All BLUE units destroyed'
        elif not red_alive:
            reason = 'All RED units destroyed'
        elif self.tick >= self.max_ticks:
            reason = 'Time limit reached'
        if reason:
            self.finish(reason)

    def finish(self, reason: str):
        """Winner determination (SC-5)."""
        bh, rh = self.hvt('BLUE'), self.hvt('RED')
        b_dead = bh is not None and not bh.alive
        r_dead = rh is not None and not rh.alive
        if b_dead and r_dead:
            out, why = 'STALEMATE', 'Both HVTs destroyed'
        elif r_dead:
            out, why = 'BLUE', 'BLUE destroyed the RED HVT'
        elif b_dead:
            out, why = 'RED', 'RED destroyed the BLUE HVT'
        elif self.kills['BLUE'] > self.kills['RED']:
            out, why = 'BLUE', f"No HVT destroyed — BLUE wins on kills {self.kills['BLUE']}–{self.kills['RED']}"
        elif self.kills['RED'] > self.kills['BLUE']:
            out, why = 'RED', f"No HVT destroyed — RED wins on kills {self.kills['RED']}–{self.kills['BLUE']}"
        else:
            out, why = 'DRAW', f"No HVT destroyed and kills level {self.kills['BLUE']}–{self.kills['RED']}"
        self.outcome = out
        self.reason = f'{reason}. {why}.'
        self.status = 'ENDED'
        self.emit({'t': 'END', 'outcome': out, 'reason': self.reason})

    def result(self) -> dict:
        dep = {s: sum(1 for u in self.units.values() if u.side == s and not u.is_hvt and not u.avatar) for s in SIDES}
        alive = {s: sum(1 for u in self.units.values() if u.side == s and not u.is_hvt and not u.avatar and u.alive) for s in SIDES}
        hvt_hp = {s: (round(self.hvt(s).hp / self.hvt(s).max_hp * 100, 1) if self.hvt(s) else 0) for s in SIDES}
        return dict(outcome=self.outcome, reason=self.reason, ticks=self.tick, sim_time=round(self.sim_time, 1),
                    kills=dict(self.kills), deployed=dep, lost={s: dep[s] - alive[s] for s in SIDES},
                    surviving=alive, hvt_hp=hvt_hp)

    # ── player soldiers (avatars) ───────────────────────────────────────────
    def avatar_of(self, player: str) -> Unit | None:
        for u in self.units.values():
            if u.avatar and u.player == player:
                return u
        return None

    def _rider_of(self, vid: str, player: str) -> bool:
        a = self.avatar_of(player)
        return bool(a and a.mounted_in == vid)

    def spawn_avatar(self, side: str, player: str, callsign: str, near=None) -> Unit:
        """A human player's soldier, spawned beside the own HVT (or a given point)."""
        a = self.avatar_of(player)
        if a is not None:
            return a
        from .inventory import ROLE_IDS
        spec = self.platforms.get(ROLE_IDS[side]['inf'])
        if spec is None:
            return None
        safe = ''.join(ch for ch in callsign.upper() if ch.isalnum())[:10] or 'PLAYER'
        uid = f"{'B' if side == 'BLUE' else 'R'}-SOL-{safe}"
        n = 2
        while uid in self.units:
            uid = f"{'B' if side == 'BLUE' else 'R'}-SOL-{safe}{n}"; n += 1
        x, y = near or self._spawn_point(side)
        u = Unit(uid, side, None, spec, self.weapons, x, y, bearing(x, y, *self.zone_centroid(enemy_of(side))), 'HOLD')
        u.name = callsign[:16]
        u.avatar, u.player, u.controller, u.detached, u.task = True, player, player, True, 'MANUAL'
        self._place(u)
        self.units[uid] = u
        self.emit({'t': 'ALERT', 'side': side, 'level': 'info', 'text': f'{callsign} deployed on foot'})
        return u

    def pick_item(self, u: Unit, item_id):
        return None  # loot exists only in Village Skirmish

    def remove_avatar(self, player: str):
        """Take a disconnected player's soldier off the battlefield (their vehicle returns to the AI)."""
        a = self.avatar_of(player)
        if a is None:
            return None
        self._exit_vehicle(player)
        for v in self.units.values():
            if v.pickup == player:
                v.pickup = None
        self.units.pop(a.id, None)
        self.drive.pop(a.id, None)
        return a

    def _spawn_point(self, side: str):
        h = self.hvt(side)
        cx, cy = (h.x, h.y) if h and h.alive else self.zone_centroid(side)
        # own stream: players joining or respawning must not perturb the battle's seeded RNG
        self.spawn_n = getattr(self, 'spawn_n', 0) + 1
        sr = random.Random(zlib.crc32(f'{self.seed}:{side}:{self.spawn_n}'.encode()))
        a = sr.uniform(0, 2 * math.pi)
        r = sr.uniform(25, 45)
        return self.terrain.clamp_xy(cx + math.cos(a) * r, cy + math.sin(a) * r)

    def _respawns(self):
        for u in self.units.values():
            if not u.avatar or u.alive or u.respawn_t <= 0:
                continue
            u.respawn_t -= self.dt
            if u.respawn_t <= 0:
                u.respawn_t = 0
                u.x, u.y = self._spawn_point(u.side)
                u.status, u.hp, u.morale, u.destroyed_t = 'OK', u.max_hp, u.morale_base, None
                u.mobility_kill = u.firepower_kill = False
                u.ammo = {w: self.weapons[w]['ammo'] for w in u.ammo}
                u.reload = {w: 0.0 for w in u.ammo}
                self._place(u)
                self.emit({'t': 'ALERT', 'side': u.side, 'level': 'info', 'text': f'{u.name} respawned at the HVT'})

    def _enter_vehicle(self, side: str, player: str):
        a = self.avatar_of(player)
        if a is None or not a.alive or a.mounted_in:
            return
        best, bd = None, 1e9
        for v in self.units.values():
            if v.side != side or not v.alive or v.is_hvt or v.avatar or v.cls == 'INF':
                continue
            if v.controller not in ('AI', '', None, player):
                continue
            reach = max(v.spec['dims']['length'], v.spec['dims']['width']) / 2 + (6 if v.is_air else 5)
            d = math.hypot(v.x - a.x, v.y - a.y)
            if v.is_air and v.alt > 30 and v.servicing <= 0:
                continue  # can only board aircraft on the ground
            if d < reach and d < bd:
                best, bd = v, d
        if best is None:
            self.emit({'t': 'ALERT', 'side': side, 'level': 'info', 'text': f'{a.name}: no friendly vehicle within reach'})
            return
        a.mounted_in = best.id
        a.speed = 0
        best.pickup = None
        best.controller = player
        best.task = 'MANUAL'
        self.drive.pop(best.id, None)
        self.emit({'t': 'ALERT', 'side': side, 'level': 'info', 'text': f'{a.name} took the controls of {best.name}'})

    def _summon(self, side: str, player: str):
        """Call a ride: the nearest friendly AI ground vehicle drives to the player's soldier and waits."""
        a = self.avatar_of(player)
        if a is None or not a.alive or a.mounted_in:
            return
        mine = [v for v in self.units.values() if v.pickup == player and v.alive]
        if mine:
            v = mine[0]
        else:
            cands = [v for v in self.units.values() if v.side == side and v.alive and not v.is_hvt and not v.is_air
                     and not v.avatar and v.controller == 'AI' and not v.mobility_kill and v.fuel > 0 and not v.pickup
                     and v.spec['cls'] not in ('SPG', 'MLRS', 'AD')]
            if not cands:
                self.emit({'t': 'ALERT', 'side': side, 'level': 'warn', 'text': f'{a.name}: no vehicle free to pick you up'})
                return
            v = min(cands, key=lambda c: math.hypot(c.x - a.x, c.y - a.y))
        d = math.hypot(v.x - a.x, v.y - a.y)
        eta = d / max(3.0, v.spec['offroad_kph'] / 3.6 * 0.7)
        v.pickup, v.pickup_until = player, self.sim_time + max(120.0, eta * 2.5) + 60  # then it waits a minute
        v.detached, v.task, v.route, v.path = True, 'MOVE', [], []
        self.emit({'t': 'ALERT', 'side': side, 'level': 'info',
                   'text': f'{v.name} en route to pick up {a.name} — {d:.0f} m, ETA {eta:.0f} s'})

    def _pickup_target(self, u: Unit):
        a = self.avatar_of(u.pickup)
        if (a is None or not a.alive or u.controller != 'AI' or self.sim_time > u.pickup_until
                or (a.mounted_in and a.mounted_in != u.id)):
            u.pickup = None
            u.detached, u.task, u.route, u.path = False, 'IDLE', [], []
            return False
        d = math.hypot(a.x - u.x, a.y - u.y)
        if d < 6:
            return None, 0  # parked beside the soldier
        if d > 80:
            u.route = [[a.x, a.y]]
            return self._follow_path(u, u.route, owner=u), 1.0
        return (a.x, a.y), clamp(d / 50, 0.2, 0.8)

    def _exit_vehicle(self, player: str):
        a = self.avatar_of(player)
        if a is None or not a.mounted_in:
            return
        v = self.units.get(a.mounted_in)
        self._eject(a, v, hurt=0)
        if v and v.alive:
            v.controller = 'AI'
            v.task = 'IDLE'
            v.detached = False  # back to its formation
            v.route, v.path = [], []
            self.drive.pop(v.id, None)

    def _eject(self, a: Unit, v: Unit | None, hurt: float):
        a.mounted_in = None
        if v is not None:
            side_off = v.spec['dims']['width'] / 2 + 2.5
            h = math.radians(v.heading)
            a.x, a.y = self.terrain.clamp_xy(v.x - math.cos(h) * side_off, v.y + math.sin(h) * side_off)
            a.heading = v.heading
        a.speed = 0
        self._place(a)
        if hurt:
            a.hp = max(1.0, a.hp - hurt)
            a.last_hit_t = self.sim_time

    def _ride(self, a: Unit):
        v = self.units.get(a.mounted_in)
        if v is None or not v.alive:
            a.mounted_in = None
            return
        a.x, a.y, a.z, a.heading = v.x, v.y, v.z, v.heading

    def _move_infantry(self, u: Unit):
        """On-foot movement: walk / sprint / crouch / strafe, slowed by slope and ground, swims in deep water."""
        manual = u.controller not in ('AI', '', None)
        inp = (self.drive.get(u.id) or {}) if manual else {}
        fwd = clamp(float(inp.get('throttle', 0)), -1, 1)
        strafe = clamp(float(inp.get('strafe', 0)), -1, 1)
        if 'turret_yaw' in inp:
            u.turret_yaw = float(inp['turret_yaw']) % 360
            u.gun_pitch = clamp(float(inp.get('gun_pitch', 0)), -60, 60)
        if 'weapon' in inp and u.loadout:
            wi = int(clamp(int(inp['weapon']), 0, len(u.loadout) - 1))
            if wi != u.cur_w:
                u.cur_w, u.mag_t = wi, 0.0  # switching weapons cancels a magazine change
        u.stance = 'CROUCH' if inp.get('crouch') else 'STAND'
        yaw = math.radians(u.turret_yaw)
        vx = math.sin(yaw) * fwd + math.cos(yaw) * strafe
        vy = math.cos(yaw) * fwd - math.sin(yaw) * strafe
        mag = math.hypot(vx, vy)
        depth = self.terrain.water_depth(u.x, u.y)
        swimming = depth > 1.3
        if mag > 1e-3:
            vx, vy = vx / mag, vy / mag
            base = 0.9 if swimming else 1.0 if u.stance == 'CROUCH' or u.heal_t > 0 else 5.8 if (inp.get('sprint') and fwd > 0.3) else 2.0
            ground = self.terrain.ground_at(u.x, u.y)
            gf = {'SAND': 0.85, 'MUD': 0.7, 'SNOW': 0.75, 'MARSH': 0.6, 'ICE': 0.8, 'ROCK': 0.85}.get(ground, 1.0)
            heading = math.degrees(math.atan2(vx, vy)) % 360
            grade = self.terrain.grade_along(u.x, u.y, heading)
            sf = clamp(1 - max(0.0, grade) / 90, 0.3, 1.0) * (1.08 if grade < -5 else 1.0)
            if grade > 110 and not swimming:  # too steep to scramble up
                sf = 0
            speed = base * (1 if swimming else gf * sf)
            nx, ny = self.terrain.clamp_xy(u.x + vx * speed * self.dt, u.y + vy * speed * self.dt)
            nx, ny = self.collide_move(u, u.x, u.y, nx, ny)
            speed = math.hypot(nx - u.x, ny - u.y) / self.dt
            u.x, u.y = nx, ny
            u.speed = speed * (1 if fwd >= -0.1 else -1)
            u.grade = grade
        else:
            u.speed = 0.0
        u.heading = u.turret_yaw
        u.throttle = fwd
        u.swimming = swimming
        u.z = self.ground_z(u.x, u.y) - (1.2 if swimming else 0)
        u.ground = 'WATER' if swimming else self.terrain.ground_at(u.x, u.y)
        u.slope = self.terrain.slope_at(u.x, u.y)
        u.pitch = u.roll = 0.0
        u.status = 'DAMAGED' if u.hp < u.max_hp else 'OK'

    # ── frames & fog-of-war views (LS-7) ────────────────────────────────────
    def frame(self) -> dict:
        """Full, unfiltered frame — persisted for replay; never sent to players as-is."""
        return dict(
            battle_id=self.id, tick=self.tick, sim_time=round(self.sim_time, 2), ts=int(time.time() * 1000),
            status=self.status, hour=round(self.hour, 2),
            units=[u.view(with_route=True) for u in self.units.values()],
            projectiles=[p.view() for p in self.projectiles if self.sim_time >= p.born],
            events=self.events,
            formations=[f.view() for f in self.formations.values()],
            detected={s: sorted(self.detected[s]) for s in SIDES},
            hvt_acquired=dict(self.hvt_acquired), kills=dict(self.kills), outcome=self.outcome, reason=self.reason,
            step_ms=round(self.step_ms, 1),
        )


def filter_frame(fr: dict, audience: str, fog: bool) -> dict:
    """Per-audience view: a player only receives hostiles inside their own detection envelope."""
    if audience == 'ADMIN' or not fog or fr.get('status') == 'ENDED':
        out = dict(fr)
        if audience != 'ADMIN':
            out['events'] = [e for e in fr['events'] if e.get('side') in (None, 'ALL', audience)]
            out['formations'] = [f for f in fr['formations'] if f['s'] == audience]
        return out
    side = audience
    seen = set(fr['detected'][side])
    acquired = fr['hvt_acquired'][side]
    units = []
    visible = set()
    for u in fr['units']:
        if u['s'] == side or u['s'] == 'NEUT' or u['id'] in seen or (u['hvt'] and acquired):
            if u['s'] != side:
                u = {k: v for k, v in u.items() if k not in ('rt', 'am', 'rl', 'fu', 'tg', 'd', 'tk', 'ctl', 'pl', 'pk')}
            units.append(u)
            visible.add(u['id'])
    own = {u['id'] for u in fr['units'] if u['s'] == side}
    events = []
    for e in fr['events']:
        t = e['t']
        if t in ('DETECT', 'HVT_ACQUIRED', 'INTRUSION', 'ALERT'):
            if e.get('side') in (side, 'ALL'):
                events.append(e)
        elif t == 'FIRE':
            if e['shooter'] in visible or e.get('target') in own:
                events.append(e)
        elif t == 'IMPACT':
            if e.get('target') in own or e.get('target') in visible or e.get('target') is None:
                events.append(e)
        elif t == 'KILL':
            if e['victim'] in own or e['victim'] in visible or e.get('attacker') in own:
                events.append(e)
        else:
            events.append(e)
    projectiles = [p for p in fr['projectiles'] if p['s'] == side or p['sh'] in visible or p.get('tg') in own]
    return dict(fr, units=units, events=events, projectiles=projectiles,
                formations=[f for f in fr['formations'] if f['s'] == side],
                detected={side: fr['detected'][side]}, hvt_acquired={side: acquired})
