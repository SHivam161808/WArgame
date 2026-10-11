"""Vehicle dynamics, gunnery and armour penetration (SKILL.md §5)."""
from __future__ import annotations

import math

from .geo import DEG, angle_diff, clamp

G = 9.81

# Rolling resistance, traction coefficient, speed-cap factor, dust signature, turn factor.
GROUND = {
    'ROAD': dict(label='Road / corridor', crr=0.02, mu=0.9, speed=1.0, dust=0.6, turn=1.0),
    'GRASS': dict(label='Farmland / grass', crr=0.06, mu=0.75, speed=0.85, dust=0.3, turn=1.0),
    'SAND': dict(label='Soft sand', crr=0.13, mu=0.55, speed=0.68, dust=1.4, turn=0.85),
    'MUD': dict(label='Mud', crr=0.17, mu=0.42, speed=0.55, dust=0.0, turn=0.75),
    'SNOW': dict(label='Snow', crr=0.10, mu=0.38, speed=0.7, dust=0.2, turn=0.8),
    'ICE': dict(label='Ice', crr=0.025, mu=0.12, speed=0.75, dust=0.0, turn=0.45),
    'ROCK': dict(label='Rock / scree', crr=0.07, mu=0.7, speed=0.6, dust=0.5, turn=0.85),
    'SALT': dict(label='Salt pan', crr=0.035, mu=0.7, speed=0.95, dust=0.9, turn=1.0),
    'MARSH': dict(label='Marsh', crr=0.22, mu=0.35, speed=0.42, dust=0.0, turn=0.7),
    'WATER': dict(label='Open water', crr=0.0, mu=0.0, speed=0.0, dust=0.0, turn=0.6),
}


def terrain_speed_cap(spec: dict, ground: str, on_corridor: bool, water_depth: float) -> tuple[float, bool, bool]:
    """(cap m/s, swimming, blocked) before slope effects."""
    if water_depth > spec['ford_depth_m']:
        if spec['swim_kph'] > 0:
            return spec['swim_kph'] / 3.6, True, False
        return 0.0, False, True
    if spec.get('hover'):
        g = 0.85 if ground == 'WATER' else 0.6 if ground == 'ROCK' else 1.0
        return (spec['road_kph'] if on_corridor else spec['offroad_kph']) / 3.6 * g, ground == 'WATER', False
    gp = GROUND['ROAD' if on_corridor else ground]
    base = spec['road_kph'] if on_corridor else spec['offroad_kph']
    cap = base / 3.6 * (1.0 if on_corridor else gp['speed'] / 0.85)
    if water_depth > 0.3:
        cap *= 0.45  # fording
    return min(cap, spec['road_kph'] / 3.6), False, False


def step_mobility(spec: dict, speed: float, throttle: float, grade_pct: float, ground: str, water_depth: float,
                  on_corridor: bool, traction: float, altitude_m: float, dt: float, mobility_kill: bool,
                  has_fuel: bool) -> dict:
    """Longitudinal dynamics.

    Tractive force = min(P/v, mu·m·g·cosθ) against gravity, rolling resistance and drag, so a
    vehicle slows on climbs, gains speed on descents (brakes permitting) and stalls above its
    gradient limit. Returns dict(speed, blocked, swimming, slip, cap_kph).
    """
    theta = math.atan(grade_pct / 100.0)
    grade_deg = theta / DEG
    cap, swimming, blocked = terrain_speed_cap(spec, ground, on_corridor, water_depth)
    if mobility_kill or not has_fuel or spec['power_kw'] <= 0:
        v = speed * max(0.0, 1 - 2.5 * dt)
        return dict(speed=0.0 if abs(v) < 0.05 else v, blocked=False, swimming=swimming, slip=False, cap_kph=0.0)
    if blocked:
        return dict(speed=0.0, blocked=True, swimming=False, slip=False, cap_kph=0.0)

    mass = spec['mass_t'] * 1000
    gp = GROUND[ground]
    mu = (0.5 if spec.get('hover') else gp['mu']) * traction
    crr = 0.015 if spec.get('hover') else 0.04 if swimming else gp['crr']
    power = spec['power_kw'] * 1000 * clamp(1 - max(0.0, altitude_m - 1500) / 10000, 0.55, 1.0)
    target = cap * throttle
    v = speed

    if not swimming and grade_deg > spec['max_grade_deg'] and throttle > 0:
        v_new = max(-0.8, v - (G * math.sin(theta) * 0.4 + 2) * dt)
        return dict(speed=v_new, blocked=True, swimming=swimming, slip=True, cap_kph=0.0)

    cos_t = math.cos(theta)
    f_trac_max = mu * mass * G * cos_t
    v_abs = max(abs(v), 1.2)
    f_engine = min(power * 0.82 / v_abs, f_trac_max)
    f_grav = -mass * G * math.sin(theta)
    sv = (v > 0) - (v < 0)
    f_roll = -sv * crr * mass * G * cos_t
    f_aero = -sv * (900 if swimming else 3) * v * v
    thr = abs(throttle) or 0.3

    if target > v + 0.02:
        f_drive = min(f_trac_max, mass * 5) if v < 0 else f_engine * thr
    elif target < v - 0.02:
        f_drive = -min(f_trac_max, mass * 6) if v > 0 else -f_engine * 0.6 * thr
    else:
        f_drive = clamp(-(f_grav + f_roll + f_aero), -f_trac_max, f_engine)
    f_drive = clamp(f_drive, -f_trac_max, f_trac_max)

    a = (f_drive + f_grav + f_roll + f_aero) / mass
    v_new = v + a * dt
    if f_drive > 0 and v <= target < v_new:
        v_new = max(target, v + f_grav / mass * dt)
    if f_drive < 0 and v >= target > v_new:
        v_new = min(target, v + f_grav / mass * dt)
    if target == 0 and sv != 0 and ((v_new > 0) - (v_new < 0)) != sv and abs(f_grav) < f_trac_max:
        v_new = 0.0
    slip = v_new > target + 0.5 and f_drive < 0
    v_new = clamp(v_new, -cap * 0.4 - 0.5, cap * (1.35 if slip else 1.08) + 0.5)
    return dict(speed=v_new, blocked=False, swimming=swimming, slip=slip, cap_kph=cap * 3.6)


def turn_rate(spec: dict, ground: str, slope_deg: float, speed: float, traction: float) -> float:
    if spec.get('air'):
        return spec['turn_rate_dps'] * 1.5
    g = 0.9 if spec.get('hover') else GROUND[ground]['turn']
    slope = clamp(1 - slope_deg / 60, 0.4, 1.0)
    spd = 0.7 if abs(speed) > 12 else 1.0
    return spec['turn_rate_dps'] * g * slope * spd * clamp(traction, 0.5, 1.0)


def fuel_burn(spec: dict, dist_m: float, dt: float, grade_pct: float, ground: str, weather_fuel: float) -> float:
    terrain = 1 + max(0.0, grade_pct) / 25 + GROUND[ground]['crr'] * 2
    return (dist_m / 1000 * spec['fuel_l_per_km'] * terrain + spec['idle_burn_lph'] * dt / 3600) * weather_fuel


# ─── Gunnery ─────────────────────────────────────────────────────────────────

def hit_probability(w: dict, rng_m: float, shooter_speed: float, stabilised: bool, crew_skill: float, morale: float,
                    target_speed: float, target_w: float, target_h: float, conceal: float, weather_disp: float,
                    light: float, thermal: bool) -> float:
    """Gaussian dispersion against the presented target box (erf in x and y), salvo-combined."""
    if rng_m > w['range_m'] or rng_m < w['min_range_m']:
        return 0.0
    if w['guided']:
        base = 0.9 - rng_m / w['range_m'] * 0.15
        return clamp(base * (1 - conceal * 0.25) * (0.85 + 0.15 * crew_skill), 0.05, 0.97)
    flight = rng_m / w['muzzle_vel']
    motion = 1 + ((0.35 if stabilised else 1.4) if shooter_speed > 1 else 0) + min(1.5, target_speed * flight / 3)
    skill = 1.55 - crew_skill * 0.8 + (0.4 if morale < 30 else 0)
    vis = 1.0 if thermal else 1 + (1 - light) * 0.8
    sigma = w['dispersion_mrad'] / 1000 * rng_m * motion * skill * weather_disp * vis + 0.05
    px = math.erf(target_w / (2 * math.sqrt(2) * sigma))
    py = math.erf(target_h / (2 * math.sqrt(2) * sigma))
    p = px * py
    if w['salvo'] > 1:
        p = 1 - (1 - p) ** w['salvo']
    return clamp(p * (1 - conceal * 0.45), 0.0, 0.99)


def presented_size(dims: dict, target_heading: float, bearing_from_shooter: float, top_down: bool = False) -> tuple[float, float]:
    if top_down:
        return dims['width'], dims['length']
    rel = angle_diff(target_heading, bearing_from_shooter) * DEG
    return abs(dims['length'] * math.sin(rel)) + abs(dims['width'] * math.cos(rel)), dims['height']


def impact_facet(target_heading: float, shot_from_bearing: float, top_attack: bool) -> tuple[str, float]:
    """(facet, obliquity°). shot_from_bearing = bearing from target to shooter."""
    if top_attack:
        return 'top', 10.0
    rel = abs(angle_diff(target_heading, shot_from_bearing))
    if rel <= 40:
        return 'front', rel
    if rel >= 145:
        return 'rear', 180 - rel
    return 'side', abs(90 - rel)


def pen_at_range(w: dict, rng_m: float) -> float:
    """KE / rail / autocannon lose ~5 %/km; chemical energy is range independent."""
    if w['type'] in ('KE', 'RAILGUN', 'AUTOCANNON'):
        return w['pen_mm'] * clamp(1 - rng_m / 20000, 0.5, 1.0)
    return w['pen_mm']


def effective_armour(armour: dict, facet: str, obliquity: float) -> float:
    return armour[facet] / max(0.42, math.cos(obliquity * DEG))
