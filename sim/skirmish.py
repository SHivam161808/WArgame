"""Village Skirmish: squads of up to 10 (humans, the rest bots) fight through a procedural village.

PUBG-style rules: everybody starts with a pistol; rifles, SMGs, shotguns, marksman and sniper rifles, RPGs,
ammunition, medkits, vests and helmets lie around the houses; cars, bikes, jeeps and two tanks stand parked
(neutral until somebody drives them); no respawns — the last squad standing wins (time limit → kills).
Line of sight, bullets and movement respect every wall, door and window of the village (sim/village.py).
"""
from __future__ import annotations

import math
import random
import zlib

from . import combat
from .engine import SIDES, Battle, enemy_of
from .geo import angle_diff, bearing, clamp
from .units import Unit
from .village import BODY_R, NavGraph, Walls, inside_rect

NEUT = 'NEUT'  # side of an empty vehicle
SQUAD = 10  # players per side; empty places are filled by bots (cfg['squad'] overrides)
START_WEAPON = 'pistol-p9'
MAX_WEAPONS = 3
SIGHT_M = 260.0
VEHICLE_PLATFORM = {'car': 'v-car', 'bike': 'v-bike', 'jeep': 'v-jeep', 'tank': 'v-tank'}
BOT_NAMES = {'BLUE': ['Arjun', 'Kabir', 'Vikram', 'Rohan', 'Dev', 'Ishaan', 'Aarav', 'Karan', 'Aditya', 'Nikhil',
                      'Rahul', 'Sameer'],
             'RED': ['Ghost', 'Viper', 'Raven', 'Jackal', 'Cobra', 'Wolf', 'Hawk', 'Shadow', 'Scorpion', 'Falcon',
                     'Reaper', 'Python']}
_geometry_cache: dict = {}


def village_geometry(terrain):
    """Walls + walking graph for a village terrain (cached: building the graph takes about a second)."""
    lay = terrain.meta['village']
    key = (lay['seed'], len(lay['walls']), id(terrain))
    g = _geometry_cache.get(key)
    if g is None:
        walls = Walls(lay)
        graph = NavGraph(lay, walls, lambda x, y: terrain.water_depth(x, y) > 1.0)
        if len(_geometry_cache) > 6:
            _geometry_cache.pop(next(iter(_geometry_cache)))
        g = _geometry_cache[key] = (walls, graph)
    return g


class Skirmish(Battle):
    mode = 'SKIRMISH'

    def __init__(self, battle_id, cfg, terrain, corridors, zones, catalogue, seed, nav=None):
        super().__init__(battle_id, cfg, terrain, corridors, zones, catalogue, seed, nav=nav)
        self.layout = terrain.meta['village']
        self.play_half = self.layout['play_half']
        self.walls, self.graph = village_geometry(terrain)
        self.items = {it['id']: dict(it) for it in self.layout['loot']}
        self.items_dirty = True
        self._item_seq = len(self.items)
        self.spawn_k = {s: 0 for s in SIDES}
        self.squad = max(1, int(cfg.get('squad') or SQUAD))
        self.agents = {s: BotSquad(self, s) for s in SIDES}
        self.friendly_fire = False  # bullets pass through squad mates
        self.bot_errors: list = []
        self._buckets: dict = {}
        for k, b in enumerate(self.layout['buildings']):
            r = math.hypot(b['w'], b['d']) / 2
            for i in range(int((b['cx'] - r) // 16), int((b['cx'] + r) // 16) + 1):
                for j in range(int((b['cy'] - r) // 16), int((b['cy'] + r) // 16) + 1):
                    self._buckets.setdefault((i, j), []).append(b)
        self._park_vehicles()

    # ── setup ───────────────────────────────────────────────────────────────
    def _park_vehicles(self):
        for n, v in enumerate(self.layout['vehicles']):
            spec = self.platforms.get(VEHICLE_PLATFORM[v['k']])
            if spec is None:
                continue
            uid = f"V-{v['k'].upper()}-{n + 1}"
            u = Unit(uid, NEUT, None, spec, self.weapons, v['x'], v['y'], v['h'], 'HOLD')
            u.name = spec['name']
            u.controller, u.task, u.detached = 'PARKED', 'PARKED', True
            self._place(u)
            self.units[uid] = u

    def deploy(self, side, plan):
        return None

    def auto_plan(self, side, inventory=None, weighted=False):
        return dict(side=side, formations=[], hvt=None)

    def start(self):
        for side in SIDES:
            have = sum(1 for u in self.units.values() if u.side == side and u.cls == 'INF')
            for _ in range(max(0, self.squad - have)):
                self.spawn_bot(side)
        self.status = 'RUNNING'
        self.emit({'t': 'ALERT', 'side': 'ALL', 'level': 'info', 'text': 'Match started — loot up, last squad standing wins'})

    def _equip(self, u: Unit):
        w = self.weapons[START_WEAPON]
        u.loadout = [START_WEAPON]
        u.ammo = {START_WEAPON: w['mag'] * 2}
        u.mag = {START_WEAPON: w['mag']}
        u.reload = {START_WEAPON: 0.0}
        u.cur_w = 0
        u.meds, u.vest, u.helmet = 0, False, False

    def spawn_bot(self, side: str) -> Unit:
        from .inventory import ROLE_IDS
        spec = self.platforms[ROLE_IDS[side]['inf']]
        used = {u.name for u in self.units.values()}
        name = next((n for n in BOT_NAMES[side] if n not in used), f'Bot-{len(self.units)}')
        uid = f"{'B' if side == 'BLUE' else 'R'}-BOT-{name.upper()}"
        x, y = self._spawn_point(side)
        u = Unit(uid, side, None, spec, self.weapons, x, y, bearing(x, y, 0, 0), 'AGGRESSIVE')
        u.name, u.bot, u.controller, u.detached, u.task = name, True, 'BOT', True, 'MANUAL'
        u.turret_yaw = u.heading
        self._equip(u)
        self._place(u)
        self.units[uid] = u
        self.agents[side].add(u)
        return u

    def spawn_avatar(self, side, player, callsign, near=None):
        fresh = self.avatar_of(player) is None
        u = super().spawn_avatar(side, player, callsign, near)
        if u is not None and fresh:
            self._equip(u)
        return u

    def _spawn_point(self, side: str):
        pts = self.layout['spawns'][side]
        k = self.spawn_k[side]
        self.spawn_k[side] += 1
        x, y = pts[k % len(pts)]
        return x + (k // len(pts)) * 2.0, y

    # ── geometry hooks ──────────────────────────────────────────────────────
    def wall_ray(self, ox, oy, oz, dx, dy, dz, max_d):
        t = self.walls.ray(ox, oy, oz, ox + dx * max_d, oy + dy * max_d, oz + dz * max_d)
        return None if t is None else t * max_d

    def wall_clear(self, ax, ay, az, bx, by, bz) -> bool:
        return self.walls.clear(ax, ay, az, bx, by, bz)

    def collide_move(self, u, x0, y0, x1, y1):
        h = self.play_half
        x1, y1 = clamp(x1, -h, h), clamp(y1, -h, h)
        return self.walls.collide(x0, y0, x1, y1, BODY_R)

    def building_at(self, x, y):
        for b in self._buckets.get((int(x // 16), int(y // 16)), ()):
            if inside_rect(x, y, b, 0.2):
                return b
        return None

    def ground_z(self, x, y):
        b = self.building_at(x, y)
        return b['zb'] if b else self.terrain.surface(x, y)

    def vehicle_blocked(self, u, x, y):
        d = u.spec['dims']
        if abs(x) > self.play_half + 5 or abs(y) > self.play_half + 5:
            return True
        if not self.walls.vehicle_blocked(x, y, u.heading, d['length'], d['width']):
            return False
        # never trap a vehicle that already overlaps something: let it drive out
        return not self.walls.vehicle_blocked(u.x, u.y, u.heading, d['length'], d['width'])

    def eye(self, u: Unit) -> float:
        if u.cls == 'INF':
            return u.z + (1.15 if u.stance == 'CROUCH' else 1.62)
        return u.eye_z()

    def sees(self, o: Unit, e: Unit) -> bool:
        """Line of sight from o's eyes to e's head or chest past terrain and walls."""
        ex, ey = o.x, o.y
        ez = self.eye(o)
        if e.cls == 'INF':
            h, _ = combat.soldier_box(e)
            pts = (e.z + h - 0.15, e.z + h * 0.6)
        else:
            pts = (e.z + e.spec['dims']['height'] * 0.8, e.centre_z())
        for tz in pts:
            if self.walls.clear(ex, ey, ez, e.x, e.y, tz) and self.terrain.los(ex, ey, ez, e.x, e.y, tz):
                return True
        return False

    # ── detection (fog of war) ──────────────────────────────────────────────
    def _detect(self):
        sight = max(70.0, SIGHT_M * max(self.light, 0.3) * self.weather['visibility'])
        for side in SIDES:
            enemy = enemy_of(side)
            obs = [u for u in self.units.values() if u.alive and u.side == side and not u.mounted_in]
            found = set()
            for e in self.units.values():
                if not e.alive or e.side != enemy or e.mounted_in:
                    continue
                loud = self.sim_time - e.last_fired < 1.0
                for o in obs:
                    d = math.hypot(e.x - o.x, e.y - o.y)
                    if d < 6 or (loud and d < 60):  # footsteps and gunshots close by
                        found.add(e.id)
                        break
                    if d > sight * (1.5 if e.cls != 'INF' else 1.0):
                        continue
                    if self.sees(o, e):
                        found.add(e.id)
                        break
            new = found - self.detected[side]
            self.detected[side] = found
            for eid in sorted(found):
                e = self.units[eid]
                self.last_seen[side][eid] = (e.x, e.y, e.heading, self.tick)
            for eid in sorted(new):
                self.emit({'t': 'DETECT', 'side': side, 'target': eid})

    # ── vehicles: anyone can drive an empty one ────────────────────────────
    def _enter_vehicle(self, side: str, player: str):
        a = self.avatar_of(player)
        if a is None or not a.alive or a.mounted_in:
            return
        best, bd = None, 1e9
        for v in self.units.values():
            if not v.alive or v.cls == 'INF' or v.is_hvt:
                continue
            if not ((v.side == NEUT and v.controller == 'PARKED') or (v.side == side and v.controller in ('PARKED', player))):
                continue
            reach = max(v.spec['dims']['length'], v.spec['dims']['width']) / 2 + 3.0  # keep in step with hud.js prompt()
            d = math.hypot(v.x - a.x, v.y - a.y)
            if d < reach and d < bd and self.walls.clear(a.x, a.y, a.z + 1.0, v.x, v.y, v.z + 1.0):
                best, bd = v, d
        if best is None:
            return
        a.mounted_in = best.id
        a.speed = 0
        best.side, best.controller, best.task = side, player, 'MANUAL'
        self.drive.pop(best.id, None)
        self.emit({'t': 'ALERT', 'side': side, 'level': 'info', 'text': f'{a.name} is driving the {best.name}'})

    def _exit_vehicle(self, player: str):
        a = self.avatar_of(player)
        v = self.units.get(a.mounted_in) if a and a.mounted_in else None
        super()._exit_vehicle(player)
        if v is not None and v.alive:
            v.side, v.controller, v.task = NEUT, 'PARKED', 'PARKED'
            v.detached = True

    def _eject(self, a, v, hurt):
        super()._eject(a, v, hurt)
        if v is not None and self.walls.overlaps(a.x, a.y, BODY_R):
            a.x, a.y = 2 * v.x - a.x, 2 * v.y - a.y  # other side of the vehicle
            self._place(a)

    def _summon(self, side, player):
        self.emit({'t': 'ALERT', 'side': side, 'level': 'info', 'text': 'No ride service here — find a car or bike'})

    def after_move(self):
        """Roadkill: fast vehicles knock down soldiers of the other side."""
        for v in self.units.values():
            if not v.alive or v.cls == 'INF' or abs(v.speed) < 6 or v.side == NEUT:
                continue
            reach = max(1.3, v.spec['dims']['length'] * 0.45)
            for s in list(self.units.values()):
                if s.cls != 'INF' or not s.alive or s.mounted_in or s.side == v.side:
                    continue
                if math.hypot(s.x - v.x, s.y - v.y) < reach:
                    dmg = abs(v.speed) * 7
                    s.hp = max(0.0, s.hp - dmg)
                    s.last_hit_t = self.sim_time
                    self.emit({'t': 'IMPACT', 'at': [round(s.x, 1), round(s.y, 1), round(s.z + 1, 1)], 'hit': True,
                               'weapon': None, 'target': s.id, 'dmg': round(dmg), 'effect': 'ROADKILL', 'delay': 0})
                    if s.hp <= 0:
                        driver = next((u for u in self.units.values() if u.mounted_in == v.id), None)
                        self.destroy(s, driver, None, 0)

    # ── loot ────────────────────────────────────────────────────────────────
    def _drop(self, kind, x, y, z):
        self._item_seq += 1
        iid = f'd{self._item_seq}'
        self.items[iid] = dict(id=iid, k=kind, x=round(x + self.rng.uniform(-0.7, 0.7), 2),
                               y=round(y + self.rng.uniform(-0.7, 0.7), 2), z=round(z + 0.05, 2))
        self.items_dirty = True

    def nearest_item(self, u: Unit, r=2.6):
        best, bd = None, r
        for it in self.items.values():
            d = math.hypot(it['x'] - u.x, it['y'] - u.y)
            if d < bd and abs(it['z'] - u.z) < 1.6:
                best, bd = it, d
        return best

    def pick_item(self, u: Unit, item_id=None):
        it = self.items.get(item_id) if item_id else None
        if it is None or math.hypot(it['x'] - u.x, it['y'] - u.y) > 2.8:
            it = self.nearest_item(u)
        if it is None or not self.walls.clear(u.x, u.y, u.z + 1.0, it['x'], it['y'], it['z'] + 0.25):
            return None
        k = it['k']
        if k in self.weapons:
            w = self.weapons[k]
            if k in u.loadout:
                if u.ammo.get(k, 0) >= w['ammo']:
                    return None
                u.ammo[k] = min(w['ammo'], u.ammo.get(k, 0) + w['mag'] * 2)
            else:
                if len(u.loadout) >= MAX_WEAPONS:  # swap the weapon in hand, leave it on the floor
                    idx = min(u.cur_w, len(u.loadout) - 1)
                    old = u.loadout[idx]
                    self._drop(old, u.x, u.y, u.z)
                    u.loadout[idx] = k
                    for d in (u.ammo, u.mag, u.reload):
                        d.pop(old, None)
                else:
                    u.loadout.append(k)
                    idx = len(u.loadout) - 1
                u.mag[k], u.ammo[k], u.reload[k] = w['mag'], w['mag'] * 2, 0.0
                u.cur_w, u.mag_t = idx, 0.0
        elif k == 'ammo':
            if all(u.ammo.get(wid, 0) >= self.weapons[wid]['ammo'] for wid in u.loadout):
                return None
            for wid in u.loadout:
                w = self.weapons[wid]
                u.ammo[wid] = min(w['ammo'], u.ammo.get(wid, 0) + w['mag'] * 2)
        elif k == 'medkit':
            if u.meds >= 5:
                return None
            u.meds += 1
        elif k == 'vest':
            if u.vest:
                return None
            u.vest = True
        elif k == 'helmet':
            if u.helmet:
                return None
            u.helmet = True
        del self.items[it['id']]
        self.items_dirty = True
        self.emit({'t': 'PICK', 'side': u.side, 'unit': u.id, 'k': k})
        return k

    # ── outcome ─────────────────────────────────────────────────────────────
    def destroy(self, t, shooter, w, rng_m):
        was_alive = t.alive
        super().destroy(t, shooter, w, rng_m)
        t.respawn_t = 0.0  # no respawns
        if was_alive and t.cls == 'INF':  # death crate: everything they carried
            for wid in t.loadout:
                if wid != START_WEAPON:
                    self._drop(wid, t.x, t.y, t.z)
            for _ in range(t.meds):
                self._drop('medkit', t.x, t.y, t.z)
            if t.vest:
                self._drop('vest', t.x, t.y, t.z)
            if t.helmet:
                self._drop('helmet', t.x, t.y, t.z)
            if any(t.ammo.values()):
                self._drop('ammo', t.x, t.y, t.z)
            self.drive.pop(t.id, None)

    def squad_alive(self) -> dict:
        return {s: sum(1 for u in self.units.values() if u.side == s and u.cls == 'INF' and u.alive) for s in SIDES}

    def _check_end(self):
        a = self.squad_alive()
        if not a['BLUE'] or not a['RED']:
            self.finish('Squad eliminated')
        elif self.tick >= self.max_ticks:
            self.finish('Time limit reached')

    def finish(self, reason: str):
        a = self.squad_alive()
        k = self.kills
        if a['BLUE'] and not a['RED']:
            out, why = 'BLUE', f"BLUE wiped out the RED squad ({a['BLUE']} left standing)"
        elif a['RED'] and not a['BLUE']:
            out, why = 'RED', f"RED wiped out the BLUE squad ({a['RED']} left standing)"
        elif not a['RED'] and not a['BLUE']:
            out, why = 'DRAW', 'Nobody is left standing'
        elif k['BLUE'] != k['RED']:
            out = 'BLUE' if k['BLUE'] > k['RED'] else 'RED'
            why = f"{out} wins on kills {max(k.values())}–{min(k.values())}"
        elif a['BLUE'] != a['RED']:
            out = 'BLUE' if a['BLUE'] > a['RED'] else 'RED'
            why = f'{out} has more soldiers standing'
        else:
            out, why = 'DRAW', f"Kills level {k['BLUE']}–{k['RED']}"
        self.outcome, self.reason, self.status = out, f'{reason}. {why}.', 'ENDED'
        self.emit({'t': 'END', 'outcome': out, 'reason': self.reason})

    def result(self) -> dict:
        dep = {s: sum(1 for u in self.units.values() if u.side == s and u.cls == 'INF') for s in SIDES}
        alive = self.squad_alive()
        return dict(outcome=self.outcome, reason=self.reason, ticks=self.tick, sim_time=round(self.sim_time, 1),
                    kills=dict(self.kills), deployed=dep, lost={s: dep[s] - alive[s] for s in SIDES}, surviving=alive,
                    hvt_hp={s: 0 for s in SIDES}, mode='SKIRMISH')

    def frame(self) -> dict:
        fr = super().frame()
        fr['mode'] = 'SKIRMISH'
        fr['alive'] = self.squad_alive()
        if self.items_dirty or self.tick % 50 == 0:
            fr['items'] = [[i['id'], i['k'], i['x'], i['y'], i['z']] for i in self.items.values()]
            self.items_dirty = False
        return fr


# ── bots ────────────────────────────────────────────────────────────────────

class _Bot:
    __slots__ = ('rng', 'mode', 'goal', 'path', 'path_t', 'target', 'seen_t', 'react', 'last_seen', 'vis', 'vis_t',
                 'look', 'pitch', 'burst_on', 'burst_t', 'strafe', 'strafe_t', 'crouch', 'err', 'err_t', 'stuck',
                 'aim_base', 'look_t', 'side_t', 'item', 'hunt_t', 'unstick_t', 'unstick')

    def __init__(self, seed, heading, hunter=False):
        self.rng = random.Random(seed)
        self.mode = 'LOOT'
        self.goal = None
        self.path = []
        self.path_t = -99.0
        self.target = None
        self.seen_t = -99.0
        self.react = 0.5
        self.last_seen = None
        self.vis = []
        self.vis_t = -99.0
        self.look = heading
        self.pitch = 0.0
        self.burst_on = False
        self.burst_t = 0.0
        self.strafe = 1
        self.strafe_t = 0.0
        self.crouch = False
        self.err = (0.0, 0.0)
        self.err_t = 0.0
        self.stuck = None
        self.aim_base = self.rng.uniform(1.2, 2.0)  # degrees: how good this bot is
        self.look_t = 0.0
        self.side_t = 0.0
        self.item = None
        self.hunt_t = self.rng.uniform(10, 25) if hunter else self.rng.uniform(25, 60)
        self.unstick_t = -1.0
        self.unstick = (0.0, 0.0)


class BotSquad:
    """Drives every bot of one side through the same drive inputs a human sends."""

    def __init__(self, b: Skirmish, side: str):
        self.b = b
        self.side = side
        self.st: dict = {}

    def add(self, u: Unit):
        self.st[u.id] = _Bot(zlib.crc32(f'{self.b.seed}:{u.id}'.encode()), u.heading, hunter=self.side == 'RED')

    def fire_support(self):
        return None

    def update(self):
        b = self.b
        for uid in list(self.st):
            u = b.units.get(uid)
            if u is None or not u.alive:
                b.drive.pop(uid, None)
                continue
            try:
                self.think(u, self.st[uid])
            except Exception as e:  # a bot must never take the match down
                b.drive[uid] = {}
                if len(b.bot_errors) < 20:
                    b.bot_errors.append(f'{uid}: {type(e).__name__}: {e}')
        self._callouts()

    def _callouts(self):
        """A bot in a fight calls the contact out: idle squad mates within 150 m come to search there."""
        b = self.b
        fights = [(b.units[uid], s) for uid, s in sorted(self.st.items())
                  if s.mode == 'ENGAGE' and s.last_seen is not None and b.units.get(uid) is not None and b.units[uid].alive]
        if not fights:
            return
        for uid, s in sorted(self.st.items()):
            u = b.units.get(uid)
            if u is None or not u.alive or s.mode not in ('PATROL', 'LOOT') or u.mounted_in:
                continue
            near = min(fights, key=lambda f: math.hypot(f[0].x - u.x, f[0].y - u.y))
            if math.hypot(near[0].x - u.x, near[0].y - u.y) < 150:
                s.last_seen = near[1].last_seen
                s.item, s.goal, s.path = None, None, []
                s.mode = 'SEARCH'

    # perception ---------------------------------------------------------
    def _perceive(self, u, s):
        b = self.b
        now = b.sim_time
        if now - s.vis_t < 0.2:
            return
        s.vis_t = now
        vis = []
        for e in b.units.values():
            if not e.alive or e.side != enemy_of(self.side) or e.mounted_in:
                continue
            d = math.hypot(e.x - u.x, e.y - u.y)
            if d > SIGHT_M:
                continue
            ahead = abs(angle_diff(s.look, bearing(u.x, u.y, e.x, e.y))) < 75
            if (d < 10 or ahead or now - u.last_hit_t < 1.5) and b.sees(u, e):
                vis.append((d, e.id))
            elif now - e.last_fired < 0.8 and d < 140 and s.target is None:  # heard a shot
                s.last_seen = (e.x + s.rng.uniform(-8, 8), e.y + s.rng.uniform(-8, 8), now)
                if s.mode not in ('ENGAGE',):
                    s.mode = 'SEARCH'
        s.vis = [eid for _, eid in sorted(vis)]

    # weapons ------------------------------------------------------------
    def _best_weapon(self, u, d):
        b = self.b
        best, score = 0, -1e9
        for i, wid in enumerate(u.loadout):
            w = b.weapons[wid]
            if u.mag.get(wid, 0) <= 0 and u.ammo.get(wid, 0) <= 0:
                continue
            sc = w['damage'] * (w.get('pellets', 1)) / max(0.08, w['reload_s'])
            if d > w['range_m'] * 0.8:
                sc *= 0.2
            if wid == 'at-rpg':
                sc *= 0.05
            if wid == START_WEAPON:
                sc *= 0.5
            if sc > score:
                best, score = i, sc
        return best

    # movement -----------------------------------------------------------
    def _go(self, u, s, goal, now, repath=3.0):
        if goal is None:
            return 0.0, 0.0
        if s.goal is None or math.hypot(goal[0] - s.goal[0], goal[1] - s.goal[1]) > 4 or now - s.path_t > repath or not s.path:
            s.goal = goal
            s.path_t = now
            s.path = self.b.graph.path((u.x, u.y), goal) or [tuple(goal)]
        while s.path and math.hypot(s.path[0][0] - u.x, s.path[0][1] - u.y) < 1.1:
            s.path.pop(0)
        if not s.path:
            return 0.0, 0.0
        wx, wy = s.path[0]
        dx, dy = wx - u.x, wy - u.y
        L = math.hypot(dx, dy) or 1
        return dx / L, dy / L

    def think(self, u: Unit, s: _Bot):
        b = self.b
        now = b.sim_time
        self._perceive(u, s)
        target = b.units.get(s.vis[0]) if s.vis else None
        if target is not None:
            if s.target != target.id:
                s.target, s.seen_t = target.id, now
                s.react = s.rng.uniform(0.45, 0.9)
            s.mode = 'ENGAGE'
            s.last_seen = (target.x, target.y, now)
        elif s.mode == 'ENGAGE':
            s.target = None
            s.mode = 'SEARCH'
        if s.mode == 'SEARCH' and (s.last_seen is None or now - s.last_seen[2] > 15):
            s.mode = 'PATROL'
        mx = my = 0.0
        fire = sprint = False
        weapon = u.cur_w
        ads = False
        # healing and reloading when nobody is in sight
        if target is None:
            if u.hp < 55 and u.meds > 0 and u.heal_t <= 0:
                u.meds -= 1
                u.heal_t, u.mag_t = 4.0, 0.0
            elif u.loadout and u.mag_t <= 0:
                wid = u.loadout[min(u.cur_w, len(u.loadout) - 1)]
                w = b.weapons[wid]
                if w.get('mag') and u.mag.get(wid, 0) < w['mag'] * 0.4:
                    combat.start_reload(b, u, wid)
        if s.mode == 'ENGAGE' and target is not None:
            d = math.hypot(target.x - u.x, target.y - u.y)
            weapon = self._best_weapon(u, d)
            wid = u.loadout[weapon]
            w = b.weapons[wid]
            if weapon != u.cur_w:
                s.seen_t = max(s.seen_t, now - s.react + 0.35)  # weapon swap takes a moment
            tz = (combat.soldier_box(target)[1] + 0.15) if target.cls == 'INF' else target.centre_z()
            ez = b.eye(u) - 0.1
            want = bearing(u.x, u.y, target.x, target.y)
            want_p = math.degrees(math.atan2(tz - ez, max(0.5, d)))
            turn = 540 * b.dt
            s.look = (s.look + clamp(angle_diff(s.look, want), -turn, turn)) % 360
            s.pitch = want_p
            # aim error: large when the target first appears, settling as the bot tracks it
            settle = max(0.0, 1 - (now - s.seen_t) / 1.6)
            sig = s.aim_base * (1 + 2.2 * settle) * (1.5 if abs(u.speed) > 2.5 else 1) * (1.3 if target.stance == 'CROUCH' else 1)
            if now - s.err_t > 0.15:
                s.err_t = now
                s.err = (s.rng.gauss(0, sig), s.rng.gauss(0, sig * 0.6))
            ay, ap = math.radians(s.look + s.err[0]), math.radians(s.pitch + s.err[1])
            ox, oy, oz = combat.muzzle(u)
            aim = [ox + math.sin(ay) * math.cos(ap) * d, oy + math.cos(ay) * math.cos(ap) * d, oz + math.sin(ap) * d]
            on_target = abs(angle_diff(s.look, want)) < 6
            ready = now - s.seen_t > s.react and d < w['range_m'] * 0.95
            if ready and on_target:
                if w.get('auto'):
                    if now > s.burst_t:
                        s.burst_on = not s.burst_on
                        s.burst_t = now + (s.rng.uniform(0.3, 0.7) if s.burst_on else s.rng.uniform(0.2, 0.5) * (1 + d / 120))
                    fire = s.burst_on
                else:
                    fire = u.reload.get(wid, 0) <= 0 and s.rng.random() < (0.5 if d < 60 else 0.25)
            ads = d > 25
            # movement while fighting: close in, back off, or strafe
            right = (math.cos(math.radians(s.look)), -math.sin(math.radians(s.look)))
            fwd = (math.sin(math.radians(s.look)), math.cos(math.radians(s.look)))
            if now > s.strafe_t:
                s.strafe = s.rng.choice((-1, 1, 1, -1, 0))
                s.strafe_t = now + s.rng.uniform(0.7, 1.8)
                s.crouch = d > 35 and s.rng.random() < 0.45
            keep = min(w['range_m'] * 0.45, 70)
            if d > keep:
                mx, my = self._go(u, s, (target.x, target.y), now, repath=1.5)
                s.crouch = False
            elif d < 5:
                mx, my = -fwd[0], -fwd[1]
            else:
                mx, my = right[0] * s.strafe, right[1] * s.strafe
            if s.crouch:
                mx, my = mx * 0.5, my * 0.5
            s.item = None
        else:
            s.crouch = False
            goal = None
            if s.mode == 'SEARCH' and s.last_seen is not None:
                goal = s.last_seen[:2]
                if math.hypot(goal[0] - u.x, goal[1] - u.y) < 3:
                    s.last_seen = None
                    s.mode = 'PATROL'
            if s.mode in ('LOOT', 'PATROL'):
                want_loot = (len(u.loadout) < 2 or u.meds == 0 and u.hp < 80
                             or all(u.ammo.get(x, 0) + u.mag.get(x, 0) < b.weapons[x].get('mag', 10) for x in u.loadout))
                if want_loot and (s.item is None or s.item not in b.items):
                    s.item = self._find_loot(u, s)
                if s.item in b.items:
                    it = b.items[s.item]
                    goal = (it['x'], it['y'])
                    if math.hypot(it['x'] - u.x, it['y'] - u.y) < 1.6:
                        b.pick_item(u, it['id'])
                        s.item = None
                        goal = None
                    s.mode = 'LOOT'
                else:
                    s.item = None
                    s.mode = 'PATROL'
            if s.mode == 'PATROL':
                s.hunt_t -= b.dt
                if s.hunt_t <= 0:  # hunt: head for where an enemy roughly is
                    s.hunt_t = s.rng.uniform(15, 30) if self.side == 'RED' else s.rng.uniform(25, 50)
                    foes = [e for e in b.units.values() if e.alive and e.side == enemy_of(self.side) and e.cls == 'INF']
                    if self.side == 'RED':  # the RED squad goes looking for the human players first
                        foes = [e for e in foes if e.avatar] or foes
                    if foes:
                        e = s.rng.choice(foes)
                        s.goal = None
                        s.last_seen = (e.x + s.rng.uniform(-35, 35), e.y + s.rng.uniform(-35, 35), now)
                        s.mode = 'SEARCH'
                        goal = s.last_seen[:2]
                if goal is None:
                    if s.goal is None or math.hypot(s.goal[0] - u.x, s.goal[1] - u.y) < 2.5 or not s.path:
                        goal = self._patrol_goal(u, s)
                    else:
                        goal = s.goal
            if goal is not None:
                mx, my = self._go(u, s, goal, now)
                sprint = math.hypot(goal[0] - u.x, goal[1] - u.y) > 25 and s.mode != 'SEARCH'
            # look where you walk, glancing around now and then
            if mx or my:
                want = math.degrees(math.atan2(mx, my)) % 360
                if now > s.look_t:
                    s.look_t = now + s.rng.uniform(1.5, 4)
                    s.side_t = now + 0.6
                    s.strafe = s.rng.choice((-1, 1))
                if now < s.side_t:
                    want = (want + 50 * s.strafe) % 360
                s.look = (s.look + clamp(angle_diff(s.look, want), -300 * b.dt, 300 * b.dt)) % 360
            s.pitch = 0.0
            aim = None
        # unstick: no progress for 1.5 s while trying to move → sidestep and re-plan
        moving = abs(mx) + abs(my) > 0.1
        if moving:
            if s.stuck is None or math.hypot(u.x - s.stuck[0], u.y - s.stuck[1]) > 0.5:
                s.stuck = (u.x, u.y, now)
            elif now - s.stuck[2] > 1.5:
                s.path, s.goal = [], None
                s.unstick_t = now + 0.7
                a = s.rng.uniform(0, 6.283)
                s.unstick = (math.sin(a), math.cos(a))
                s.stuck = (u.x, u.y, now)
        if now < s.unstick_t:
            mx, my = s.unstick
        yaw = math.radians(s.look)
        fwd_in = mx * math.sin(yaw) + my * math.cos(yaw)
        str_in = mx * math.cos(yaw) - my * math.sin(yaw)
        b.drive[u.id] = dict(throttle=round(clamp(fwd_in, -1, 1), 2), strafe=round(clamp(str_in, -1, 1), 2),
                             sprint=sprint and fwd_in > 0.5, crouch=s.crouch, turret_yaw=round(s.look, 1),
                             gun_pitch=round(s.pitch, 1), fire=fire, weapon=weapon, aim=aim, ads=ads)

    def _find_loot(self, u, s):
        b = self.b
        best, bd = None, 90.0
        have = set(u.loadout)
        for it in b.items.values():
            k = it['k']
            if k in have and len(u.loadout) > 1:
                continue
            if k == 'medkit' and u.meds >= 2 or k in ('vest', 'helmet') and getattr(u, k):
                continue
            if k in b.weapons and k == START_WEAPON:
                continue
            if k == 'ammo' and len(u.loadout) < 2:
                continue
            d = math.hypot(it['x'] - u.x, it['y'] - u.y) * (0.7 if k in b.weapons else 1.0)
            if d < bd:
                best, bd = it['id'], d
        return best

    def _patrol_goal(self, u, s):
        b = self.b
        enemy = b.layout['spawns'][enemy_of(self.side)][0]
        rooms = [r for bl in b.layout['buildings'] for r in bl['rooms']]
        # drift toward the middle and the enemy side of the map
        cands = s.rng.sample(rooms, min(12, len(rooms))) if rooms else []
        cands += [(s.rng.uniform(-b.play_half + 20, b.play_half - 20), s.rng.uniform(-b.play_half + 20, b.play_half - 20)) for _ in range(4)]
        def score(p):
            d_me = math.hypot(p[0] - u.x, p[1] - u.y)
            d_en = math.hypot(p[0] - enemy[0], p[1] - enemy[1])
            return -abs(d_me - 90) * 0.5 - d_en * 0.25 + s.rng.uniform(0, 40)
        best = max(cands, key=score) if cands else (0.0, 0.0)
        return tuple(best)
