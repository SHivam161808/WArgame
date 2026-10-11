"""Runtime unit / formation / projectile state and their compact wire views."""
from __future__ import annotations

from dataclasses import dataclass, field


class Unit:
    __slots__ = (
        'id', 'side', 'formation', 'platform_id', 'cls', 'name', 'spec', 'x', 'y', 'z', 'heading', 'pitch', 'roll',
        'speed', 'turret_yaw', 'gun_pitch', 'hp', 'max_hp', 'morale', 'morale_base', 'fuel', 'fuel_max', 'ammo',
        'reload', 'status', 'mobility_kill', 'firepower_kill', 'doctrine', 'task', 'target_id', 'route', 'path',
        'path_goal', 'path_t', 'detached', 'controller', 'ground', 'swimming', 'slope', 'grade', 'throttle', 'kills',
        'shots', 'aps', 'last_fired', 'alt', 'avenue', 'is_hvt', 'destroyed_t', 'servicing', 'blocked_t',
        'last_hit_t', 'cap_kph', 'slot', 'orbit', 'intercept_id', 'home',
        'avatar', 'mounted_in', 'respawn_t', 'stance', 'player', 'pickup', 'pickup_until',
        'loadout', 'mag', 'mag_t', 'mag_w', 'cur_w', 'heal_t', 'meds', 'vest', 'helmet', 'bot',
    )

    def __init__(self, uid: str, side: str, formation: str | None, spec: dict, catalogue_weapons: dict,
                 x: float, y: float, heading: float, doctrine: str):
        self.id = uid
        self.side = side
        self.formation = formation
        self.platform_id = spec['id']
        self.cls = spec['cls']
        self.name = uid
        self.spec = spec
        self.x, self.y, self.z = x, y, 0.0
        self.heading = heading
        self.pitch = self.roll = 0.0
        self.speed = 0.0
        self.turret_yaw = heading
        self.gun_pitch = 0.0
        self.hp = self.max_hp = float(spec['hp'])
        self.morale_base = 70 + 30 * spec.get('crew_skill', 0.75)
        self.morale = self.morale_base
        self.fuel = self.fuel_max = float(spec['fuel_l'])
        self.ammo = {w: catalogue_weapons[w]['ammo'] for w in spec['weapons'] if w in catalogue_weapons}
        self.reload = {w: 0.0 for w in self.ammo}
        self.status = 'OK'
        self.mobility_kill = False
        self.firepower_kill = False
        self.doctrine = doctrine
        self.task = 'IDLE'
        self.target_id: str | None = None
        self.route: list = []  # own waypoints (detached units)
        self.path: list = []  # expanded A* path currently followed
        self.path_goal = None
        self.path_t = -1e9
        self.detached = False
        self.controller = 'AI'
        self.ground = 'GRASS'
        self.swimming = False
        self.slope = 0.0
        self.grade = 0.0
        self.throttle = 0.0
        self.kills = 0
        self.shots = 0
        self.aps = (spec.get('aps') or {}).get('charges', 0)
        self.last_fired = -1e9
        self.alt = float((spec.get('air') or {}).get('cruise_alt_m', 0))
        self.avenue = -1
        self.is_hvt = spec['cls'] == 'HVT'
        self.destroyed_t: float | None = None
        self.servicing = 0.0
        self.blocked_t = 0.0
        self.last_hit_t = -1e9
        self.cap_kph = 0.0
        self.slot = 0
        self.orbit = 0.0
        self.intercept_id: str | None = None
        self.home = (x, y)
        self.avatar = False  # a human player's soldier
        self.pickup = None  # player id this vehicle is driving to collect
        self.pickup_until = 0.0
        self.mounted_in: str | None = None  # vehicle the soldier is riding/driving
        self.respawn_t = 0.0
        self.stance = 'STAND'
        self.player: str | None = None
        # small arms (Village Skirmish): weapons carried, rounds in the magazine, magazine change / healing timers
        self.loadout: list = [w for w in spec['weapons'] if w in catalogue_weapons]
        self.mag: dict = {w: catalogue_weapons[w]['mag'] for w in self.loadout if catalogue_weapons[w].get('mag')}
        self.mag_t = 0.0
        self.mag_w: str | None = None
        self.cur_w = 0
        self.heal_t = 0.0
        self.meds = 0
        self.vest = False
        self.helmet = False
        self.bot = False

    @property
    def alive(self) -> bool:
        return self.status != 'DESTROYED'

    @property
    def is_air(self) -> bool:
        return bool(self.spec.get('air'))

    def eye_z(self) -> float:
        mast = 6.0 if self.cls == 'RECON' else 0.0
        return self.z + self.spec['dims']['height'] * 0.9 + mast

    def centre_z(self) -> float:
        return self.z + self.spec['dims']['height'] * 0.5

    def view(self, with_route: bool = False) -> dict:
        r = round
        v = {
            'id': self.id, 's': self.side, 'f': self.formation, 'p': self.platform_id, 'c': self.cls, 'n': self.name,
            'x': r(self.x, 1), 'y': r(self.y, 1), 'z': r(self.z, 1), 'h': r(self.heading, 1), 'pi': r(self.pitch, 1),
            'ro': r(self.roll, 1), 'v': r(self.speed, 2), 'ty': r(self.turret_yaw, 1), 'gp': r(self.gun_pitch, 1),
            'hp': r(self.hp, 1), 'mhp': self.max_hp, 'm': r(self.morale), 'fu': r(self.fuel), 'fm': self.fuel_max,
            'am': self.ammo, 'rl': {k: r(v, 1) for k, v in self.reload.items()}, 'st': self.status,
            'mk': self.mobility_kill, 'fk': self.firepower_kill, 'd': self.doctrine, 'tk': self.task,
            'tg': self.target_id, 'gr': self.ground, 'sw': self.swimming, 'sl': r(self.slope, 1), 'gd': r(self.grade, 1),
            'th': r(self.throttle, 2), 'k': self.kills, 'aps': self.aps, 'alt': r(self.alt), 'hvt': self.is_hvt,
            'ctl': self.controller, 'cap': r(self.cap_kph),
        }
        if self.pickup:
            v['pk'] = self.pickup
        if self.cls == 'INF':
            cw = self.loadout[min(self.cur_w, len(self.loadout) - 1)] if self.loadout else None
            v.update(wp=self.loadout, cw=cw, mg=self.mag, rlt=r(self.mag_t, 1), ht=r(self.heal_t, 1), md=self.meds,
                     ve=self.vest, hl=self.helmet, stn=self.stance)
            if self.bot:
                v['bot'] = True
        if self.avatar:
            v.update(av=True, mt=self.mounted_in, rs=r(self.respawn_t, 1), stn=self.stance, pl=self.player)
        if with_route and self.detached and self.route:
            v['rt'] = [[r(a), r(b)] for a, b in self.route]
        return v


@dataclass
class Formation:
    id: str
    side: str
    name: str
    doctrine: str
    shape: str
    unit_ids: list = field(default_factory=list)
    route: list = field(default_factory=list)  # player/AI waypoints
    path: list = field(default_factory=list)  # expanded path the leader follows
    spacing: float = 120.0
    controller: str = 'AI'  # 'AI' or player id
    fire_mission: dict | None = None
    orders_locked_until: float = -1.0  # sim time until which AI won't override human orders
    heading: float = 0.0
    avenue: int = 0

    def view(self) -> dict:
        return {'id': self.id, 's': self.side, 'n': self.name, 'd': self.doctrine, 'sh': self.shape,
                'u': self.unit_ids, 'rt': [[round(a), round(b)] for a, b in self.route], 'ctl': self.controller,
                'fm': self.fire_mission}


class Projectile:
    __slots__ = ('id', 'side', 'shooter_id', 'weapon_id', 'kind', 'x', 'y', 'z', 'frm', 'aim', 'target_id',
                 'speed', 'born', 'impact_t', 'will_hit', 'apex', 'p_hit', 'engaged')

    def __init__(self, **kw):
        self.engaged = set()  # point-defence units that already took their shot at this projectile
        for k, v in kw.items():
            setattr(self, k, v)

    def view(self) -> dict:
        return {'id': self.id, 's': self.side, 'sh': self.shooter_id, 'w': self.weapon_id, 'k': self.kind,
                'x': round(self.x, 1), 'y': round(self.y, 1), 'z': round(self.z, 1),
                'fr': [round(c, 1) for c in self.frm], 'aim': [round(c, 1) for c in self.aim],
                'tg': self.target_id, 'b': round(self.born, 2), 'it': round(self.impact_t, 2), 'ap': round(self.apex)}
