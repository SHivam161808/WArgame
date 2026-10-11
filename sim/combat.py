"""Targeting, firing, projectiles, interception, penetration and damage (PW-6, PW-7, RP-4)."""
from __future__ import annotations

import math
from typing import TYPE_CHECKING

from .geo import DEG, angle_diff, bearing, clamp
from .physics import effective_armour, hit_probability, impact_facet, pen_at_range, presented_size

if TYPE_CHECKING:
    from .engine import Battle
    from .units import Unit

CLASS_THREAT = {'INF': 0.55, 'MBT': 1.3, 'HOVER': 1.2, 'IFV': 1.0, 'GUNSHIP': 1.25, 'AD': 0.9, 'SPG': 0.95, 'MLRS': 0.95,
                'RECON': 0.8, 'APC': 0.7, 'UAV': 0.6, 'JET': 0.8, 'HVT': 1.0}


def can_engage(w: dict, target: 'Unit') -> bool:
    return w['vs_air'] if target.is_air else w['vs_ground']


def expected_damage(b: 'Battle', u: 'Unit', w: dict, t: 'Unit', rng_m: float) -> tuple[float, float]:
    """(score, p_hit) for weapon w from u against t."""
    brg = bearing(u.x, u.y, t.x, t.y)
    tw, th = presented_size(t.spec['dims'], t.heading, (brg + 180) % 360, top_down=w['indirect'] or w['top_attack'])
    p = hit_probability(w, rng_m, abs(u.speed), u.spec.get('stabilised', True), u.spec.get('crew_skill', 0.75), u.morale,
                        abs(t.speed), tw, th, 0 if t.is_air else b.terrain.conceal_at(t.x, t.y), b.weather['dispersion'],
                        b.light, u.spec['sensor'].get('thermal', False))
    if p <= 0:
        return 0.0, 0.0
    facet, obl = impact_facet(t.heading, bearing(t.x, t.y, u.x, u.y), w['top_attack'] or w['indirect'])
    eff = effective_armour(t.spec['armour'], facet, obl)
    pen = pen_at_range(w, rng_m)
    p_pen = clamp((pen / max(1.0, eff) - 0.7) / 0.6, 0.03, 1.0)
    dmg = w['damage'] * (w['salvo'] if w['type'] == 'AUTOCANNON' else 1)
    kill_val = min(1.0, dmg * p_pen / max(1.0, t.hp))
    pri = CLASS_THREAT.get(t.cls, 1.0)
    if t.is_hvt:
        pri *= 2.2 if u.doctrine in ('AGGRESSIVE', 'FLANKING') else 1.4
    return p * (0.35 + kill_val) * pri, p


def choose_shot(b: 'Battle', u: 'Unit') -> tuple | None:
    """Best (weapon_id, target, p_hit, range) among ready weapons and visible targets."""
    best = None
    best_score = 0.04
    seen = sorted(b.detected[u.side])  # sorted: set order varies per process (determinism)
    for wid, ammo in u.ammo.items():
        if ammo <= 0 or u.reload[wid] > 0:
            continue
        w = b.weapons[wid]
        if w['type'] == 'LASER':
            continue  # laser is point defence / counter-drone, handled separately
        for tid in seen:
            t = b.units.get(tid)
            if t is None or not t.alive or t.mounted_in or not can_engage(w, t):
                continue
            rng_m = math.hypot(t.x - u.x, t.y - u.y)
            if rng_m > w['range_m'] or rng_m < w['min_range_m']:
                continue
            if w['indirect']:
                continue  # indirect fire goes through fire missions / clusters
            if not w['guided'] and not b.has_los(u, t):
                continue
            if w['guided'] and not (b.has_los(u, t) or u.is_air or w['top_attack'] and rng_m < w['range_m'] * 0.8 and b.has_los_from_side(u.side, t)):
                continue
            score, p = expected_damage(b, u, w, t, rng_m)
            if t.id == u.target_id:
                score *= 1.25  # stick with the current target
            if score > best_score:
                best_score, best = score, (wid, t, p, rng_m)
    return best


def turret_ready(u: 'Unit', t: 'Unit', dt: float) -> bool:
    """Slew the turret toward the target; True when aligned."""
    want = bearing(u.x, u.y, t.x, t.y)
    d = angle_diff(u.turret_yaw, want)
    step = u.spec.get('turret_dps', 40) * dt
    if abs(d) <= step:
        u.turret_yaw = want
        return True
    u.turret_yaw = (u.turret_yaw + math.copysign(step, d)) % 360
    return abs(d) < 3


def auto_fire(b: 'Battle', u: 'Unit'):
    if u.firepower_kill or not u.ammo or u.task == 'RTB' or u.servicing > 0:
        return
    shot = choose_shot(b, u)
    if not shot:
        u.target_id = None if u.target_id and u.target_id not in b.detected[u.side] else u.target_id
        return
    wid, t, p, rng_m = shot
    u.target_id = t.id
    w = b.weapons[wid]
    if not w['guided'] and not u.is_air and not turret_ready(u, t, b.dt):
        return
    fire(b, u, w, t, p, rng_m)


def fire(b: 'Battle', u: 'Unit', w: dict, t: 'Unit', p: float, rng_m: float):
    u.ammo[w['id']] -= 1
    u.reload[w['id']] = w['reload_s'] * (1.25 if u.morale < 30 else 1.0)
    u.shots += 1
    u.last_fired = b.sim_time
    u.gun_pitch = clamp(math.degrees(math.atan2(t.centre_z() - u.eye_z(), max(1.0, rng_m))), -10, 25)
    frm = (u.x, u.y, u.eye_z())
    to = (t.x, t.y, t.centre_z())
    if w['guided']:
        launch_missile(b, u, w, t, p, frm)
        return
    hit = b.rng.random() < p
    flight = rng_m / w['muzzle_vel']
    if hit:
        imp = to
    else:
        # miss: impact scatters around the target (dust signature for the client)
        ang = b.rng.uniform(0, 2 * math.pi)
        off = b.rng.uniform(4, 25) + rng_m * 0.004
        mx, my = t.x + math.cos(ang) * off, t.y + math.sin(ang) * off
        imp = (mx, my, b.terrain.height(mx, my))
    b.emit({'t': 'FIRE', 'shooter': u.id, 'weapon': w['id'], 'from': rnd(frm), 'to': rnd(imp), 'hit': hit,
            'target': t.id, 'flight': round(flight, 2), 'fx': w['fx']})
    if hit:
        apply_hit(b, t, w, u, rng_m, imp)
    else:
        b.emit({'t': 'IMPACT', 'at': rnd(imp), 'hit': False, 'weapon': w['id'], 'target': None, 'dmg': 0,
                'effect': 'MISS', 'delay': round(flight, 2)})


def rnd(p) -> list:
    return [round(p[0], 1), round(p[1], 1), round(p[2], 1)]


def launch_missile(b: 'Battle', u: 'Unit', w: dict, t: 'Unit', p: float, frm: tuple):
    pid = b.next_id('M')
    rng_m = math.hypot(t.x - u.x, t.y - u.y)
    pr = b.new_projectile(id=pid, side=u.side, shooter_id=u.id, weapon_id=w['id'], kind='MISSILE',
                          x=frm[0], y=frm[1], z=frm[2], frm=frm, aim=(t.x, t.y, t.centre_z()), target_id=t.id,
                          speed=w['muzzle_vel'], born=b.sim_time, impact_t=b.sim_time + rng_m / w['muzzle_vel'],
                          will_hit=b.rng.random() < p, apex=max(60.0, rng_m * 0.06) if w['top_attack'] else 15.0, p_hit=p)
    b.emit({'t': 'FIRE', 'shooter': u.id, 'weapon': w['id'], 'from': rnd(frm), 'to': rnd(pr.aim), 'hit': pr.will_hit,
            'target': t.id, 'flight': round(rng_m / w['muzzle_vel'], 2), 'fx': w['fx'], 'proj': pid})


def fire_indirect(b: 'Battle', u: 'Unit', wid: str, target_xy: tuple, spread: float = 0.0) -> bool:
    """Artillery / rockets at a ground point (fire mission). Returns True if a round was fired."""
    w = b.weapons[wid]
    if u.ammo.get(wid, 0) <= 0 or u.reload.get(wid, 1) > 0 or u.firepower_kill:
        return False
    rng_m = math.hypot(target_xy[0] - u.x, target_xy[1] - u.y)
    if not (w['min_range_m'] <= rng_m <= w['range_m']):
        return False
    u.ammo[wid] -= 1
    u.reload[wid] = w['reload_s']
    u.shots += 1
    u.last_fired = b.sim_time
    u.turret_yaw = bearing(u.x, u.y, *target_xy)
    u.gun_pitch = 35 if w['fx'] == 'SHELL' else 45
    flight = clamp(rng_m / (w['muzzle_vel'] * 0.55), 3.0, 45.0)
    sigma = w['dispersion_mrad'] / 1000 * rng_m * b.weather['dispersion'] + spread
    for k in range(max(1, w['salvo'])):
        ax = target_xy[0] + b.rng.gauss(0, sigma)
        ay = target_xy[1] + b.rng.gauss(0, sigma)
        aim = (ax, ay, b.terrain.height(ax, ay))
        frm = (u.x, u.y, u.eye_z())
        pid = b.next_id('S')
        b.new_projectile(id=pid, side=u.side, shooter_id=u.id, weapon_id=wid, kind='ROCKET' if w['fx'] == 'ROCKET' else 'SHELL',
                         x=frm[0], y=frm[1], z=frm[2], frm=frm, aim=aim, target_id=None, speed=w['muzzle_vel'],
                         born=b.sim_time + k * 0.4, impact_t=b.sim_time + flight + k * 0.4, will_hit=True,
                         apex=min(12000.0, rng_m * 0.18 + 300), p_hit=1.0)
        b.emit({'t': 'FIRE', 'shooter': u.id, 'weapon': wid, 'from': rnd(frm), 'to': rnd(aim), 'hit': True,
                'target': None, 'flight': round(flight, 2), 'fx': w['fx'], 'proj': pid})
    return True


def update_projectiles(b: 'Battle'):
    keep = []
    for pr in b.projectiles:
        if b.sim_time < pr.born:
            keep.append(pr)
            continue
        w = b.weapons[pr.weapon_id]
        if pr.kind == 'MISSILE':
            t = b.units.get(pr.target_id)
            if t is not None and t.alive:
                pr.aim = (t.x, t.y, t.centre_z())
            dx, dy, dz = pr.aim[0] - pr.x, pr.aim[1] - pr.y, pr.aim[2] - pr.z
            d = math.sqrt(dx * dx + dy * dy + dz * dz)
            step = pr.speed * b.dt
            if try_intercept(b, pr):
                continue
            if d <= step:
                resolve_missile(b, pr, w)
                continue
            pr.x += dx / d * step
            pr.y += dy / d * step
            pr.z += dz / d * step
            pr.impact_t = b.sim_time + (d - step) / pr.speed
            keep.append(pr)
        else:
            # ballistic: position along the parabola for the client; impact at the planned time
            T = max(0.1, pr.impact_t - pr.born)
            f = clamp((b.sim_time - pr.born) / T, 0, 1)
            pr.x = pr.frm[0] + (pr.aim[0] - pr.frm[0]) * f
            pr.y = pr.frm[1] + (pr.aim[1] - pr.frm[1]) * f
            pr.z = pr.frm[2] + (pr.aim[2] - pr.frm[2]) * f + 4 * pr.apex * f * (1 - f)
            if f > 0.6 and try_intercept(b, pr):
                continue
            if b.sim_time >= pr.impact_t:
                blast(b, pr, w)
                continue
            keep.append(pr)
    b.projectiles = keep


def try_intercept(b: 'Battle', pr) -> bool:
    """Laser point defence on any enemy unit in range (C-RAM / anti-missile)."""
    for u in b.alive_units(enemy=pr.side):
        pd = u.spec.get('point_defence')
        if not pd or u.firepower_kill:
            continue
        if u.id in pr.engaged:
            continue
        d = math.hypot(pr.x - u.x, pr.y - u.y)
        if d > pd['range_m']:
            continue
        pr.engaged.add(u.id)  # one engagement per defender per projectile
        p = pd['p'] * (0.6 if pr.kind != 'MISSILE' else 1.0) * b.weather['thermal']
        if b.rng.random() < p:
            b.emit({'t': 'INTERCEPT', 'by': u.id, 'method': 'LASER', 'proj': pr.id, 'at': rnd((pr.x, pr.y, pr.z))})
            return True
    return False


def resolve_missile(b: 'Battle', pr, w: dict):
    t = b.units.get(pr.target_id)
    at = pr.aim
    if t is None or not t.alive:
        b.emit({'t': 'IMPACT', 'at': rnd(at), 'hit': False, 'weapon': w['id'], 'target': None, 'dmg': 0, 'effect': 'MISS', 'proj': pr.id})
        return
    if t.aps > 0 and w['type'] in ('MISSILE', 'CE'):
        p = t.spec['aps']['p'] * (0.45 if w['top_attack'] else 1.0)
        if b.rng.random() < p:
            t.aps -= 1
            b.emit({'t': 'INTERCEPT', 'by': t.id, 'method': 'APS', 'proj': pr.id, 'at': rnd(at)})
            return
    if t.is_air and w['type'] == 'SAM' and b.rng.random() < 0.18:
        pr.will_hit = False  # flares / manoeuvre
    shooter = b.units.get(pr.shooter_id)
    if pr.will_hit:
        rng_m = math.hypot(pr.frm[0] - t.x, pr.frm[1] - t.y)
        apply_hit(b, t, w, shooter, rng_m, at, proj=pr.id)
    else:
        ang = b.rng.uniform(0, 2 * math.pi)
        mx, my = t.x + math.cos(ang) * 15, t.y + math.sin(ang) * 15
        b.emit({'t': 'IMPACT', 'at': rnd((mx, my, b.terrain.height(mx, my))), 'hit': False, 'weapon': w['id'],
                'target': None, 'dmg': 0, 'effect': 'MISS', 'proj': pr.id})


def blast(b: 'Battle', pr, w: dict):
    """Area effect: damage falls off with distance, strikes top armour."""
    shooter = b.units.get(pr.shooter_id)
    R = w['blast_m'] or 10
    any_hit = False
    for t in list(b.units.values()):
        if not t.alive or t.is_air or t.mounted_in:
            continue
        d = math.hypot(t.x - pr.aim[0], t.y - pr.aim[1])
        if d > R:
            continue
        f = (1 - d / R) ** 1.5
        if t.side == pr.side:
            f *= 0.5  # friendly fire is possible but crews take cover from own fire
        any_hit = True
        apply_hit(b, t, w, shooter, math.hypot(pr.frm[0] - t.x, pr.frm[1] - t.y), pr.aim, scale=f, proj=pr.id)
    if not any_hit:
        b.emit({'t': 'IMPACT', 'at': rnd(pr.aim), 'hit': False, 'weapon': w['id'], 'target': None, 'dmg': 0,
                'effect': 'BLAST', 'proj': pr.id})


def apply_hit(b: 'Battle', t: 'Unit', w: dict, shooter: 'Unit | None', rng_m: float, at, scale: float = 1.0, proj=None,
              part: str = 'body'):
    if not t.alive:
        return
    if t.cls == 'INF':  # people: no armour facets — helmet / vest, headshots, range fall-off
        if part == 'head':
            scale *= w.get('head', 2.0) * (0.6 if t.helmet else 1.0)
        elif t.vest:
            scale *= 0.65
        if w.get('mag') and rng_m > w['range_m'] * 0.6:
            scale *= max(0.5, 1 - (rng_m - w['range_m'] * 0.6) / w['range_m'])
        salvo = w['salvo'] if w['type'] == 'AUTOCANNON' else 1
        dmg = w['damage'] * salvo * scale * b.rng.uniform(0.92, 1.08)
        t.hp = max(0.0, t.hp - dmg)
        t.last_hit_t = b.sim_time
        t.heal_t = 0.0  # getting shot interrupts a medkit
        effect = 'DESTROYED' if t.hp <= 0 else ('HEADSHOT' if part == 'head' else 'WOUNDED')
        if t.hp > 0 and t.status == 'OK':
            t.status = 'DAMAGED'
        b.emit({'t': 'IMPACT', 'at': rnd(at), 'hit': True, 'weapon': w['id'], 'target': t.id, 'dmg': round(dmg, 1),
                'effect': effect, 'part': part, 'proj': proj,
                'delay': round(rng_m / max(1, w['muzzle_vel']), 2) if not proj else 0})
        if t.hp <= 0:
            b.destroy(t, shooter, w, rng_m)
        return
    sx, sy = (shooter.x, shooter.y) if shooter else (at[0], at[1])
    top = w['top_attack'] or w['indirect']
    facet, obl = impact_facet(t.heading, bearing(t.x, t.y, sx, sy), top)
    eff = effective_armour(t.spec['armour'], facet, obl)
    pen = pen_at_range(w, rng_m) * b.rng.uniform(0.9, 1.1) * (0.4 + 0.6 * scale)
    dmg_base = w['damage'] * (w['salvo'] if w['type'] == 'AUTOCANNON' else 1) * scale
    r = b.rng.random()
    if pen > eff:
        ratio = min(1.6, pen / max(1.0, eff))
        dmg = dmg_base * (0.75 + 0.5 * b.rng.random()) * (0.7 + 0.3 * ratio)
        effect = 'DAMAGED'
        if not t.is_hvt and not t.is_air:
            if r < 0.07 * ratio * scale:
                dmg = t.hp  # catastrophic: ammunition cook-off
                effect = 'DESTROYED'
            elif r < 0.25:
                t.mobility_kill = True
                effect = 'MOBILITY_KILL'
            elif r < 0.38:
                t.firepower_kill = True
                effect = 'FIREPOWER_KILL'
    else:
        dmg = dmg_base * 0.06
        effect = 'NO_PEN'
    t.hp = max(0.0, t.hp - dmg)
    t.last_hit_t = b.sim_time
    t.morale = max(0.0, t.morale - (6 + dmg * 0.35) * (0.4 if t.is_hvt else 1))
    if t.hp <= 0:
        effect = 'DESTROYED'
    elif t.mobility_kill and t.status != 'DESTROYED':
        t.status = 'IMMOBILISED'
    elif t.status == 'OK':
        t.status = 'DAMAGED'
    b.emit({'t': 'IMPACT', 'at': rnd(at), 'hit': True, 'weapon': w['id'], 'target': t.id, 'dmg': round(dmg, 1),
            'effect': effect, 'facet': facet, 'proj': proj, 'delay': round(rng_m / max(1, w['muzzle_vel']), 2) if not proj else 0})
    if t.hp <= 0:
        b.destroy(t, shooter, w, rng_m)


def point_defence_vs_drones(b: 'Battle', u: 'Unit'):
    """Laser weapons engage drones and gunships in range."""
    for wid in u.ammo:
        w = b.weapons[wid]
        if w['type'] != 'LASER' or u.reload[wid] > 0 or u.firepower_kill:
            continue
        for tid in sorted(b.detected[u.side]):
            t = b.units.get(tid)
            if t is None or not t.alive or not t.is_air:
                continue
            d = math.hypot(t.x - u.x, t.y - u.y)
            if d > w['range_m']:
                continue
            u.reload[wid] = w['reload_s']
            u.last_fired = b.sim_time
            hit = b.rng.random() < 0.7 * b.weather['thermal']
            frm, to = (u.x, u.y, u.eye_z()), (t.x, t.y, t.z)
            b.emit({'t': 'FIRE', 'shooter': u.id, 'weapon': wid, 'from': rnd(frm), 'to': rnd(to), 'hit': hit,
                    'target': t.id, 'flight': 0, 'fx': 'BEAM'})
            if hit:
                apply_hit(b, t, w, u, d, to)
            break


def manual_fire(b: 'Battle', u: 'Unit', inp: dict):
    """Direct-control shot (DC-1): geometric ray from the muzzle toward the aim point with dispersion."""
    weapons = [wid for wid in (u.loadout or u.spec['weapons']) if wid in u.ammo]
    if not weapons or (u.firepower_kill and u.cls != 'INF'):
        return
    wid = weapons[min(len(weapons) - 1, max(0, int(inp.get('weapon', 0))))]
    w = b.weapons[wid]
    if w.get('mag'):  # small arms: magazine, rate of fire, automatic fire
        if u.mag_t > 0 or u.heal_t > 0 or u.reload[wid] > 0:
            return
        if u.mag.get(wid, 0) <= 0:
            start_reload(b, u, wid)
            return
        shots = max(1, int(b.dt / w['reload_s'] + 0.5)) if w.get('auto') else 1
        for _ in range(shots):
            if u.mag.get(wid, 0) <= 0:
                start_reload(b, u, wid)
                break
            u.mag[wid] -= 1
            _ray_shot(b, u, w, wid, inp)
        u.reload[wid] = w['reload_s']
        return
    if u.ammo[wid] <= 0 or u.reload[wid] > 0:
        return
    aim = inp.get('aim')
    if w['indirect']:
        if aim:
            fire_indirect(b, u, wid, (aim[0], aim[1]))
        return
    if abs(angle_diff(u.turret_yaw, inp.get('turret_yaw', u.turret_yaw))) > 4 and not u.is_air:
        return  # turret still slewing
    if w['guided']:
        ox, oy, oz = u.x, u.y, u.eye_z()
        dx, dy, dz = _aim_dir(u, inp, ox, oy, oz)
        # lock the visible enemy closest to the crosshair within 4°
        best, best_ang = None, 4.0
        for tid in sorted(b.detected[u.side]):
            t = b.units.get(tid)
            if t is None or not t.alive or not can_engage(w, t):
                continue
            vx, vy, vz = t.x - ox, t.y - oy, t.centre_z() - oz
            vl = math.sqrt(vx * vx + vy * vy + vz * vz) or 1
            if vl > w['range_m']:
                continue
            ang = math.degrees(math.acos(clamp((vx * dx + vy * dy + vz * dz) / vl, -1, 1)))
            if ang < best_ang:
                best, best_ang = t, ang
        if best is None:
            b.emit({'t': 'ALERT', 'side': u.side, 'level': 'info', 'text': f'{u.name}: no missile lock'})
            return
        _, p = expected_damage(b, u, w, best, math.hypot(best.x - ox, best.y - oy))
        fire(b, u, w, best, p, math.hypot(best.x - ox, best.y - oy))
        return
    u.ammo[wid] -= 1
    u.reload[wid] = w['reload_s']
    _ray_shot(b, u, w, wid, inp)


def _aim_dir(u, inp, ox, oy, oz):
    aim = inp.get('aim')
    if aim:
        dx, dy, dz = aim[0] - ox, aim[1] - oy, aim[2] - oz
    else:
        yaw, pit = u.turret_yaw * DEG, u.gun_pitch * DEG
        dx, dy, dz = math.sin(yaw) * math.cos(pit), math.cos(yaw) * math.cos(pit), math.sin(pit)
    L = math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0
    return dx / L, dy / L, dz / L


def soldier_box(t: 'Unit'):
    """(height, centre z) of a soldier's hit box — crouching makes you smaller."""
    h = 1.25 if t.stance == 'CROUCH' else (0.9 if t.swimming else 1.8)
    return h, t.z + h / 2


def muzzle(u: 'Unit'):
    if u.cls == 'INF':
        return u.x, u.y, u.z + (1.15 if u.stance == 'CROUCH' else 1.5)
    return u.x, u.y, u.eye_z()


def start_reload(b: 'Battle', u: 'Unit', wid: str):
    w = b.weapons[wid]
    if not w.get('mag') or u.mag_t > 0 or u.ammo.get(wid, 0) <= 0 or u.mag.get(wid, 0) >= w['mag']:
        return
    u.mag_t, u.mag_w = w['reload_mag_s'], wid


def tick_small_arms(b: 'Battle', u: 'Unit'):
    """Magazine changes and medkits finishing."""
    if u.mag_t > 0:
        u.mag_t -= b.dt
        if u.mag_t <= 0:
            u.mag_t = 0.0
            wid = u.mag_w
            if wid in u.ammo:
                take = min(b.weapons[wid]['mag'] - u.mag.get(wid, 0), u.ammo[wid])
                u.mag[wid] = u.mag.get(wid, 0) + take
                u.ammo[wid] -= take
    if u.heal_t > 0:
        u.heal_t -= b.dt
        if u.heal_t <= 0:
            u.heal_t = 0.0
            u.hp = min(u.max_hp, u.hp + 75)
            if u.hp >= u.max_hp and u.status == 'DAMAGED':
                u.status = 'OK'


def _ray_shot(b: 'Battle', u: 'Unit', w: dict, wid: str, inp: dict):
    """One trigger pull: ray(s) from the muzzle with gaussian dispersion; terrain, walls and units stop it."""
    ox, oy, oz = muzzle(u)
    adx, ady, adz = _aim_dir(u, inp, ox, oy, oz)
    moving = abs(u.speed) > (2.5 if u.cls == 'INF' else 1)
    steady = (0.7 if u.stance == 'CROUCH' else 1.0) * (1.0 if inp.get('ads') or u.cls != 'INF' else 1.6)
    sig = (w['dispersion_mrad'] / 1000 * (1 + (0.6 if moving else 0)) * steady * b.weather['dispersion']
           * (1.6 - u.spec.get('crew_skill', 0.75) * 0.8))
    u.shots += 1
    u.last_fired = b.sim_time
    for _ in range(int(w.get('pellets', 1))):
        dx, dy, dz = adx + b.rng.gauss(0, sig), ady + b.rng.gauss(0, sig), adz + b.rng.gauss(0, sig)
        L = math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0
        dx, dy, dz = dx / L, dy / L, dz / L
        terrain_hit = b.terrain.raycast(ox, oy, oz, dx, dy, dz, w['range_m'])
        t_hit = math.dist((ox, oy, oz), terrain_hit) if terrain_hit else w['range_m']
        tw_ = b.wall_ray(ox, oy, oz, dx, dy, dz, t_hit)
        wall = tw_ is not None
        if wall:
            t_hit = tw_
        hit_unit, part = None, 'body'
        for t in b.units.values():
            if not t.alive or t.id == u.id or t.mounted_in or t.id == u.mounted_in:
                continue
            if t.side == u.side and not b.friendly_fire:
                continue
            if t.cls == 'INF':
                th, cz = soldier_box(t)
                tw = 0.6
            else:
                tw, th = presented_size(t.spec['dims'], t.heading, bearing(t.x, t.y, ox, oy))
                cz = t.centre_z()
            vx, vy, vz = t.x - ox, t.y - oy, cz - oz
            s = vx * dx + vy * dy + vz * dz
            if s <= 0 or s > t_hit:
                continue
            px, py, pz = ox + dx * s - t.x, oy + dy * s - t.y, oz + dz * s - cz
            if math.hypot(px, py) <= tw / 2 and abs(pz) <= th / 2:
                hit_unit, t_hit, wall = t, s, False
                part = 'head' if t.cls == 'INF' and pz > th / 2 - 0.3 else 'body'
        imp = (ox + dx * t_hit, oy + dy * t_hit, oz + dz * t_hit)
        b.emit({'t': 'FIRE', 'shooter': u.id, 'weapon': wid, 'from': rnd((ox, oy, oz)), 'to': rnd(imp),
                'hit': hit_unit is not None, 'target': hit_unit.id if hit_unit else None,
                'flight': round(t_hit / w['muzzle_vel'], 2), 'fx': w['fx']})
        if hit_unit is not None:
            apply_hit(b, hit_unit, w, u, t_hit, imp, part=part)
        else:
            b.emit({'t': 'IMPACT', 'at': rnd(imp), 'hit': False, 'weapon': wid, 'target': None, 'dmg': 0,
                    'effect': 'WALL' if wall else 'MISS', 'delay': round(t_hit / w['muzzle_vel'], 2)})
        if w['blast_m'] and w.get('mag'):
            area_blast(b, u, w, imp, skip=hit_unit)


def area_blast(b: 'Battle', u: 'Unit', w: dict, at, skip=None):
    """Rocket blast: people in the open within ~2× blast_m take falling-off damage (walls shield)."""
    r = w['blast_m'] * 2.2
    for t in list(b.units.values()):
        if not t.alive or t is skip or t.mounted_in or (t.side == u.side and t is not u and not b.friendly_fire):
            continue
        d = math.dist((t.x, t.y, t.centre_z()), at)
        if d > r or not b.wall_clear(at[0], at[1], at[2] + 0.3, t.x, t.y, t.centre_z()):
            continue
        k = 1 - d / r
        dmg = w['damage'] * (0.9 if t.cls == 'INF' else 0.15) * k
        t.hp = max(0.0, t.hp - dmg)
        t.last_hit_t = b.sim_time
        b.emit({'t': 'IMPACT', 'at': rnd((t.x, t.y, t.centre_z())), 'hit': True, 'weapon': w['id'], 'target': t.id,
                'dmg': round(dmg, 1), 'effect': 'DESTROYED' if t.hp <= 0 else 'BLAST', 'delay': 0})
        if t.hp <= 0:
            b.destroy(t, u, w, d)

