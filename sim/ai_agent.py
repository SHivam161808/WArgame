"""AI commander for RED (PL-3, RP-1..4, AI-5) and for AI-filled BLUE slots (PL-5).

Each AI-controlled formation (or individually detached unit) re-evaluates its course of action
every couple of seconds from its *own side's* detection picture — never from ground truth:
hold & fire, advance to stand-off, flank at an offset, close to effective range, intercept
intruders, or fall back when outgunned / shaken.
"""
from __future__ import annotations

import math
import zlib
from typing import TYPE_CHECKING

from .geo import bearing, clamp, dist_to_polyline, point_in_polygon

if TYPE_CHECKING:
    from .engine import Battle
    from .units import Formation, Unit

REPLAN_S = 2.0


class AIAgent:
    def __init__(self, battle: 'Battle', side: str):
        self.b = battle
        self.side = side
        self.enemy = 'RED' if side == 'BLUE' else 'BLUE'
        self.last = -1e9
        self.goals: dict = {}  # key -> last goal point (avoid needless re-planning)
        self.log: list = []  # recent decisions (for the advisor / AAR)

    # ── picture ─────────────────────────────────────────────────────────────
    def picture(self) -> list['Unit']:
        b = self.b
        return [b.units[i] for i in sorted(b.detected[self.side]) if i in b.units and b.units[i].alive]

    def objective(self) -> tuple:
        b = self.b
        if b.hvt_acquired[self.side]:
            h = b.hvt(self.enemy)
            if h and h.alive:
                return h.x, h.y
        return b.zone_centroid(self.enemy)

    def avenue_path(self, idx: int) -> list:
        cs = self.b.corridors
        if not cs:
            return []
        p = cs[idx % len(cs)]['path']
        return p if self.side == 'BLUE' else list(reversed(p))

    def along_avenue(self, idx: int, x: float, y: float, goal: tuple, max_pts: int = 6) -> list:
        """Waypoints ahead along an avenue of approach, then the goal."""
        path = self.avenue_path(idx)
        if not path:
            return [list(goal)]
        k = min(range(len(path)), key=lambda i: (path[i][0] - x) ** 2 + (path[i][1] - y) ** 2)
        ahead = path[k + 1::3][:max_pts]
        if math.hypot(goal[0] - x, goal[1] - y) < 3000:
            return [list(goal)]
        return [list(p) for p in ahead] + [list(goal)]

    @staticmethod
    def points(units) -> float:
        return sum(u.spec.get('points', 3) * (u.hp / u.max_hp) for u in units)

    # ── per-tick ────────────────────────────────────────────────────────────
    def update(self):
        b = self.b
        if b.sim_time - self.last < REPLAN_S:
            return
        self.last = b.sim_time
        pic = self.picture()
        self.posture = self.global_posture(pic)
        for f in list(b.formations.values()):
            if f.side != self.side or f.controller != 'AI':
                continue
            if f.orders_locked_until > b.sim_time and (f.route or f.doctrine == 'HOLD'):
                continue  # respect the human commander's last order to this AI formation
            members = [b.units[i] for i in f.unit_ids if b.units[i].alive and not b.units[i].detached and not b.units[i].is_air]
            if members:
                self.plan_group(f.id, members, f.doctrine, f.avenue, pic, formation=f)
        for u in b.units.values():
            if u.side != self.side or not u.alive or u.is_hvt or u.controller != 'AI' or u.avatar or u.pickup:
                continue
            if u.is_air:
                self.plan_air(u, pic)
            elif u.detached and u.task not in ('HOLD', 'MOVE') or (u.detached and self.side == 'RED' and u.task != 'HOLD'):
                f = b.formations.get((u.side, u.formation))
                if f is not None and f.controller != 'AI':
                    continue
                if u.task != 'INTERCEPT':
                    self.plan_group(u.id, [u], u.doctrine, u.avenue, pic)
        self.home_defence(pic)

    def global_posture(self, pic: list) -> str:
        """MASS when clearly ahead (or the clock is running out), otherwise NORMAL (AI-5)."""
        b = self.b
        own = [u for u in b.alive_units(self.side) if not u.is_hvt]
        lost_enemy = sum(1 for u in b.units.values() if u.side == self.enemy and not u.alive and not u.is_hvt)
        total_enemy = sum(1 for u in b.units.values() if u.side == self.enemy and not u.is_hvt) or 1
        est_enemy = max(len(pic), total_enemy - lost_enemy)  # assume unseen enemies still exist
        late = b.tick > b.max_ticks * 0.65
        if (len(own) >= est_enemy * 1.4 and b.hvt_acquired[self.side]) or late:
            return 'MASS'
        return 'NORMAL'

    def set_route(self, key: str, target, route: list, formation: 'Formation | None', units: list, task: str):
        prev = self.goals.get(key)
        changed = prev is None or math.hypot(prev[0] - target[0], prev[1] - target[1]) > 450
        if formation is not None:
            if changed or not formation.route:
                formation.route = [list(map(float, p)) for p in route]
                formation.path = []
                for u in units:
                    u.path = []
            for u in units:
                if u.task not in ('INTERCEPT',):
                    u.task = task
        else:
            u = units[0]
            if changed or not u.route:
                u.route = [list(map(float, p)) for p in route]
                u.path = []
            u.task = task
        if changed:
            self.goals[key] = tuple(target)
            self.log.append((self.b.tick, key, task, [round(target[0]), round(target[1])]))
            self.log = self.log[-200:]

    def hold(self, key: str, formation, units, task='HOLD'):
        if formation is not None:
            formation.route = []
            formation.path = []
        for u in units:
            if formation is None:
                u.route = []
            u.path = []
            u.task = task
        self.goals[key] = (units[0].x, units[0].y)

    # ── doctrine behaviours (RP-3) ──────────────────────────────────────────
    def plan_group(self, key: str, units: list, doctrine: str, avenue: int, pic: list, formation=None):
        b = self.b
        cx = sum(u.x for u in units) / len(units)
        cy = sum(u.y for u in units) / len(units)
        morale = sum(u.morale for u in units) / len(units)
        near = [e for e in pic if math.hypot(e.x - cx, e.y - cy) < 5000 and not e.is_hvt]
        own_near = [u for u in b.alive_units(self.side) if math.hypot(u.x - cx, u.y - cy) < 5000 and not u.is_hvt]
        ratio = self.points(own_near) / max(1.0, self.points(near)) if near else 9.0
        goal = self.objective()
        own_hvt = b.hvt(self.side)
        home = (own_hvt.x, own_hvt.y) if own_hvt else b.zone_centroid(self.side)
        mobile = [u for u in units if not u.mobility_kill and u.fuel > 0]
        if not mobile:
            return

        # fall back when shaken or badly outgunned
        if near and (morale < 30 or ratio < 0.55) and doctrine not in ('DEFENSIVE', 'HOLD'):
            nearest = min(near, key=lambda e: math.hypot(e.x - cx, e.y - cy))
            away = bearing(nearest.x, nearest.y, cx, cy)
            back = self.cover_point(cx + math.sin(math.radians(away)) * 1800, cy + math.cos(math.radians(away)) * 1800)
            self.set_route(key, back, [back], formation, units, 'WITHDRAW')
            return

        if doctrine == 'HOLD':
            self.hold(key, formation, units)
            return

        # massing: everything except the HVT-defence role commits to the attack
        role_defence = formation is not None and formation.id == 'BRAVO' or (formation is None and units[0].formation == 'BRAVO')
        if getattr(self, 'posture', 'NORMAL') == 'MASS' and not role_defence and doctrine in ('SUPPRESSIVE', 'DEFENSIVE', 'RECON'):
            if not any(b.weapons[w]['indirect'] for u in units for w in u.ammo):
                doctrine = 'AGGRESSIVE'

        if doctrine == 'DEFENSIVE':
            d = math.hypot(home[0] - cx, home[1] - cy)
            threats = [e for e in pic if math.hypot(e.x - home[0], e.y - home[1]) < 4000]
            if threats:
                t = min(threats, key=lambda e: math.hypot(e.x - home[0], e.y - home[1]))
                p = self.standoff(home, (t.x, t.y), 700)
                self.set_route(key, p, [p], formation, units, 'ENGAGE')
            elif d > 900:
                p = self.standoff(home, b.zone_centroid(self.enemy), 600)
                self.set_route(key, p, [p], formation, units, 'MOVE')
            else:
                if formation is not None and formation.shape != 'RING' and formation.controller == 'AI':
                    formation.shape = 'RING'
                self.hold(key, formation, units)
            return

        if doctrine == 'SUPPRESSIVE':
            # hold at stand-off near own HVT, facing the enemy; artillery fires via fire_support()
            post = self.standoff(home, b.zone_centroid(self.enemy), 2500)
            if math.hypot(post[0] - cx, post[1] - cy) > 700:
                self.set_route(key, post, self.along_avenue(avenue, cx, cy, post, 3) if math.hypot(post[0] - cx, post[1] - cy) > 4000 else [post], formation, units, 'MOVE')
            else:
                self.hold(key, formation, units)
            return

        if doctrine == 'RECON':
            if near:
                t = min(near, key=lambda e: math.hypot(e.x - cx, e.y - cy))
                d = math.hypot(t.x - cx, t.y - cy)
                if d < 3000:
                    away = bearing(t.x, t.y, cx, cy)
                    p = self.cover_point(cx + math.sin(math.radians(away)) * 1500, cy + math.cos(math.radians(away)) * 1500)
                    self.set_route(key, p, [p], formation, units, 'WITHDRAW')
                else:
                    self.hold(key, formation, units, 'SCREEN')
                return
            if b.hvt_acquired[self.side]:
                h = b.hvt(self.enemy)
                p = self.standoff((h.x, h.y), (cx, cy), 5500) if h else (cx, cy)
                if math.hypot(p[0] - cx, p[1] - cy) > 600:
                    self.set_route(key, p, [p], formation, units, 'SCREEN')
                else:
                    self.hold(key, formation, units, 'SCREEN')
                return
            target = b.zone_centroid(self.enemy)
            self.set_route(key, target, self.along_avenue(avenue + 1, cx, cy, target), formation, units, 'MOVE')
            return

        if doctrine == 'FLANKING':
            sep = math.hypot(goal[0] - cx, goal[1] - cy)
            if sep > 4500 and not near:
                brg = bearing(cx, cy, goal[0], goal[1])
                side = 1 if zlib.crc32(key.encode()) % 2 else -1  # stable across runs
                lat = math.radians(brg + 90 * side)
                mid = (cx + (goal[0] - cx) * 0.55 + math.sin(lat) * 3500, cy + (goal[1] - cy) * 0.55 + math.cos(lat) * 3500)
                mid = b.terrain.clamp_xy(*mid)
                self.set_route(key, goal, [list(mid), list(goal)], formation, units, 'FLANK')
                return
            # close in from the flank
        # AGGRESSIVE (and flanking units once committed): close to effective range on the nearest threat,
        # otherwise drive on the objective along the assigned avenue.
        if near:
            t = min(near, key=lambda e: math.hypot(e.x - cx, e.y - cy))
            d = math.hypot(t.x - cx, t.y - cy)
            eff = 2200 if ratio > 1.3 else 3200
            if d > eff + 300:
                p = self.standoff((t.x, t.y), (cx, cy), eff)
                self.set_route(key, p, [p], formation, units, 'ENGAGE')
            else:
                engaged = any(u.target_id for u in units)
                if b.hvt_acquired[self.side] and ratio > 1.4:
                    self.set_route(key, goal, [list(goal)], formation, units, 'ENGAGE')
                elif not engaged:
                    # target out of sight (dead ground / concealment): close in
                    p = self.standoff((t.x, t.y), (cx, cy), max(700, d - 900))
                    self.set_route(key, p, [p], formation, units, 'ENGAGE')
                else:
                    self.hold(key, formation, units, 'ENGAGE')
            return
        self.set_route(key, goal, self.along_avenue(avenue, cx, cy, goal), formation, units, 'MOVE')

    def plan_air(self, u: 'Unit', pic: list):
        """Air units hunt detected ground targets and keep clear of known air defence."""
        if u.task == 'RTB' or u.servicing > 0:
            return
        b = self.b
        ad = [e for e in pic if e.cls == 'AD']
        targets = [e for e in pic if not e.is_air]
        u.detached = True
        if targets:
            weapons = [b.weapons[w] for w in u.ammo if u.ammo[w] > 0 and b.weapons[w]['vs_ground']]
            if not weapons:
                u.route = [list(u.home)]
                u.task = 'RTB'
                return
            rng_m = max(w['range_m'] for w in weapons) * 0.7
            score = lambda e: (3 if e.is_hvt else 1) * e.spec.get('points', 3) / (1 + math.hypot(e.x - u.x, e.y - u.y) / 4000)
            t = max(targets, key=score)
            p = self.standoff((t.x, t.y), (u.x, u.y), rng_m)
            for a in ad:
                if math.hypot(a.x - p[0], a.y - p[1]) < 9000:
                    p = self.standoff((a.x, a.y), p, 10000)  # stay outside the SAM envelope
            u.route = [list(p)]
            u.task = 'ENGAGE'
        else:
            leader_pos = b.zone_centroid(self.side)
            f = b.formations.get((u.side, u.formation))
            if f:
                ground = [b.units[i] for i in f.unit_ids if b.units[i].alive and not b.units[i].is_air]
                if ground:
                    leader_pos = (ground[0].x, ground[0].y)
            # patrol ahead of own forces toward the enemy
            goal = self.objective()
            p = (leader_pos[0] + (goal[0] - leader_pos[0]) * 0.35, leader_pos[1] + (goal[1] - leader_pos[1]) * 0.35)
            if not u.route or math.hypot(u.route[-1][0] - p[0], u.route[-1][1] - p[1]) > 1500:
                u.route = [list(p)]
            u.task = 'SCREEN'

    def home_defence(self, pic: list):
        """RP-2: dispatch the nearest units to intercept BLUE/RED intruders inside the own zone."""
        b = self.b
        zone = b.zones.get(self.side)
        if not zone:
            return
        intruders = [e for e in pic if point_in_polygon(e.x, e.y, zone)]
        # release finished interceptors
        for u in b.alive_units(self.side):
            if u.task == 'INTERCEPT' and (u.intercept_id not in {e.id for e in intruders}):
                u.intercept_id = None
                u.task = 'IDLE'
                f = b.formations.get((u.side, u.formation))
                if f and f.doctrine == u.doctrine:
                    u.detached = False
                    u.route = []
        if not intruders:
            return
        for e in intruders:
            assigned = [u for u in b.alive_units(self.side) if u.intercept_id == e.id]
            need = 2 - len(assigned)
            if need <= 0:
                continue
            pool = []
            for u in b.alive_units(self.side):
                if u.is_hvt or u.is_air or u.intercept_id or u.controller != 'AI' or u.mobility_kill or u.avatar or u.pickup:
                    continue
                f = b.formations.get((u.side, u.formation))
                if f is not None and f.controller != 'AI':
                    continue
                if u.cls in ('SPG', 'MLRS', 'AD') or not any(b.weapons[w]['vs_ground'] and not b.weapons[w]['indirect'] for w in u.ammo):
                    continue
                pool.append(u)
            pool.sort(key=lambda u: math.hypot(u.x - e.x, u.y - e.y))
            for u in pool[:need]:
                u.intercept_id = e.id
                u.detached = True
                u.task = 'INTERCEPT'
                u.route = [list(self.standoff((e.x, e.y), (u.x, u.y), 1500))]
                u.path = []
                self.log.append((b.tick, u.id, 'INTERCEPT', e.id))
                b.emit({'t': 'ALERT', 'side': self.side, 'level': 'warn', 'text': f'{u.name} dispatched to intercept intruder {e.cls}'})

    # ── fire support (CHARLIE, BP-5) ────────────────────────────────────────
    def fire_support(self):
        b = self.b
        from .combat import fire_indirect
        pic = None
        for u in b.alive_units(self.side):
            indirect = [w for w in u.ammo if b.weapons[w]['indirect'] and u.ammo[w] > 0 and u.reload[w] <= 0]
            if not indirect or u.controller not in ('AI', '', None) and u.task == 'MANUAL':
                continue
            f = b.formations.get((u.side, u.formation))
            if f and f.doctrine == 'HOLD':
                continue
            if f and f.fire_mission:
                tgt = f.fire_mission['target']
                if fire_indirect(b, u, indirect[0], tgt):
                    f.fire_mission['rounds'] -= 1
                    if f.fire_mission['rounds'] <= 0:
                        f.fire_mission = None
                        b.emit({'t': 'ALERT', 'side': self.side, 'level': 'info', 'text': f'{f.id}: fire mission complete'})
                continue
            if pic is None:
                pic = [e for e in self.picture() if not e.is_air]
                eh = b.hvt(self.enemy)
                if b.hvt_acquired[self.side] and eh is not None and eh.alive and eh not in pic:
                    pic.append(eh)  # a located command post does not move
            if not pic:
                return
            w = b.weapons[indirect[0]]
            clusters = cluster(pic, 700)
            best, best_score = None, 0
            for c in clusters:
                d = math.hypot(c['x'] - u.x, c['y'] - u.y)
                if not (w['min_range_m'] <= d <= w['range_m']):
                    continue
                if any(math.hypot(o.x - c['x'], o.y - c['y']) < w['blast_m'] * 2 for o in b.alive_units(self.side)):
                    continue  # danger close
                s = c['points'] + (60 if c['hvt'] else 0)  # a located HVT is the priority target
                if s > best_score:
                    best, best_score = c, s
            if best:
                lead = 0.0
                fire_indirect(b, u, indirect[0], (best['x'] + lead, best['y']), spread=best['r'] * 0.3)

    # ── geometry helpers ────────────────────────────────────────────────────
    def standoff(self, target, frm, dist: float) -> tuple:
        d = math.hypot(frm[0] - target[0], frm[1] - target[1]) or 1.0
        x = target[0] + (frm[0] - target[0]) / d * dist
        y = target[1] + (frm[1] - target[1]) / d * dist
        return self.b.terrain.clamp_xy(x, y)

    def cover_point(self, x: float, y: float) -> tuple:
        """Best-concealed cell within ~400 m (dead ground / vegetation)."""
        t = self.b.terrain
        best, bc = (x, y), -1
        for dx in (-400, 0, 400):
            for dy in (-400, 0, 400):
                px, py = t.clamp_xy(x + dx, y + dy)
                c = t.conceal_at(px, py)
                if c > bc:
                    best, bc = (px, py), c
        return best


def cluster(units, radius: float) -> list[dict]:
    """Greedy clustering of detected units (for fire missions and the advisor)."""
    left = list(units)
    out = []
    while left:
        seed = left.pop(0)
        members = [seed] + [u for u in left if math.hypot(u.x - seed.x, u.y - seed.y) < radius]
        left = [u for u in left if u not in members]
        x = sum(u.x for u in members) / len(members)
        y = sum(u.y for u in members) / len(members)
        out.append(dict(x=x, y=y, n=len(members), r=max(50.0, max(math.hypot(u.x - x, u.y - y) for u in members)),
                        points=sum(u.spec.get('points', 3) for u in members), hvt=any(u.is_hvt for u in members),
                        ids=[u.id for u in members], classes=[u.cls for u in members]))
    return out
