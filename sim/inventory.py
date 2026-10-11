"""Default platform & weapon repository and default order of battle.

Admins edit all of this from Admin → Assets; the engine snapshots the catalogue at battle
start (SKILL.md invariant 6). All designations are fictional.
"""
from __future__ import annotations

import copy
import math

AIR_CLASSES = {'GUNSHIP', 'JET', 'UAV'}
PLATFORM_CLASSES = ['MBT', 'IFV', 'APC', 'SPG', 'RECON', 'HOVER', 'GUNSHIP', 'JET', 'UAV', 'AD', 'MLRS', 'HVT', 'INF', 'CAR']
WEAPON_TYPES = ['KE', 'CE', 'AUTOCANNON', 'ARTILLERY', 'MISSILE', 'RAILGUN', 'LASER', 'SAM', 'SMALLARMS']
CLASS_LABEL = {
    'MBT': 'Main Battle Tank', 'IFV': 'Infantry Fighting Vehicle', 'APC': 'Armoured Personnel Carrier',
    'SPG': 'Self-Propelled Gun', 'RECON': 'Reconnaissance', 'HOVER': 'Hover Tank', 'GUNSHIP': 'Attack Gunship',
    'JET': 'Strike Fighter', 'UAV': 'Drone', 'AD': 'Air Defence', 'MLRS': 'Rocket Artillery', 'HVT': 'High-Value Target', 'INF': 'Infantry (player soldier)',
    'CAR': 'Civilian / light vehicle',
}


def _w(**kw) -> dict:
    base = dict(range_m=3000, min_range_m=0, dispersion_mrad=0.4, muzzle_vel=1500, pen_mm=100, damage=20,
                blast_m=0, ammo=20, reload_s=5, salvo=1, guided=False, top_attack=False, vs_air=False,
                vs_ground=True, indirect=False, fx='TRACER')
    base.update(kw)
    return base


DEFAULT_WEAPONS = [
    _w(id='rail-120', name='120 mm EM Railgun', type='RAILGUN', round='HV-Slug', range_m=5200, min_range_m=50, dispersion_mrad=0.16, muzzle_vel=2900, pen_mm=950, damage=72, ammo=36, reload_s=6.5, fx='RAIL'),
    _w(id='rail-90', name='90 mm Light Railgun', type='RAILGUN', round='HV-Slug', range_m=4200, min_range_m=50, dispersion_mrad=0.2, muzzle_vel=2500, pen_mm=620, damage=55, ammo=40, reload_s=4.5, fx='RAIL'),
    _w(id='apfsds-125', name='125 mm Smoothbore', type='KE', round='APFSDS', range_m=4000, min_range_m=50, dispersion_mrad=0.3, muzzle_vel=1750, pen_mm=720, damage=62, ammo=28, reload_s=7),
    _w(id='heat-125', name='125 mm Smoothbore', type='CE', round='Tandem-HEAT', range_m=3500, min_range_m=50, dispersion_mrad=0.42, muzzle_vel=950, pen_mm=760, damage=55, blast_m=4, ammo=14, reload_s=7),
    _w(id='atgm-top', name='Top-Attack ATGM', type='MISSILE', round='Tandem top-attack', range_m=6000, min_range_m=250, dispersion_mrad=0.2, muzzle_vel=290, pen_mm=1100, damage=88, blast_m=5, ammo=6, reload_s=14, guided=True, top_attack=True, fx='MISSILE'),
    _w(id='atgm-lite', name='Light Loitering ATGM', type='MISSILE', round='EFP top-attack', range_m=5000, min_range_m=200, dispersion_mrad=0.2, muzzle_vel=220, pen_mm=700, damage=60, blast_m=4, ammo=2, reload_s=10, guided=True, top_attack=True, fx='MISSILE'),
    _w(id='ac-40', name='40 mm CT Autocannon', type='AUTOCANNON', round='APFSDS-T / ABM', range_m=2600, dispersion_mrad=0.7, muzzle_vel=1500, pen_mm=130, damage=13, salvo=3, ammo=120, reload_s=1.4, vs_air=True),
    _w(id='ac-30', name='30 mm Chain Gun', type='AUTOCANNON', round='APDS-T', range_m=2200, dispersion_mrad=0.8, muzzle_vel=1100, pen_mm=75, damage=9, salvo=4, ammo=150, reload_s=1.1, vs_air=True),
    _w(id='hmg-127', name='12.7 mm RWS', type='AUTOCANNON', round='API-T', range_m=1600, dispersion_mrad=1.2, muzzle_vel=900, pen_mm=22, damage=4, salvo=6, ammo=200, reload_s=0.8, vs_air=True),
    _w(id='laser-pd', name='50 kW Laser Point-Defence', type='LASER', round='Beam', range_m=3000, dispersion_mrad=0.02, muzzle_vel=3e8, pen_mm=12, damage=7, ammo=9999, reload_s=1.5, vs_air=True, vs_ground=False, fx='BEAM'),
    _w(id='arty-155', name='155 mm Guided Howitzer', type='ARTILLERY', round='Guided HE', range_m=32000, min_range_m=2500, dispersion_mrad=1.1, muzzle_vel=900, pen_mm=140, damage=62, blast_m=38, ammo=40, reload_s=9, indirect=True, fx='SHELL'),
    _w(id='rail-arty', name='Hypersonic Rail Artillery', type='ARTILLERY', round='HVP', range_m=42000, min_range_m=3000, dispersion_mrad=0.6, muzzle_vel=2200, pen_mm=180, damage=72, blast_m=26, ammo=30, reload_s=11, indirect=True, fx='SHELL'),
    _w(id='mlrs-214', name='214 mm Guided Rockets', type='ARTILLERY', round='DPICM', range_m=45000, min_range_m=5000, dispersion_mrad=2.6, muzzle_vel=1000, pen_mm=90, damage=46, blast_m=48, salvo=6, ammo=24, reload_s=50, indirect=True, fx='ROCKET'),
    _w(id='sam-sr', name='Short-Range SAM', type='SAM', round='IIR proximity', range_m=12000, min_range_m=300, dispersion_mrad=0.2, muzzle_vel=950, pen_mm=60, damage=95, blast_m=10, ammo=8, reload_s=6, guided=True, vs_air=True, vs_ground=False, fx='MISSILE'),
    _w(id='agm-hf', name='Air-to-Ground Missile', type='MISSILE', round='Tandem top-attack', range_m=8000, min_range_m=400, dispersion_mrad=0.2, muzzle_vel=420, pen_mm=1100, damage=92, blast_m=5, ammo=8, reload_s=4, guided=True, top_attack=True, fx='MISSILE'),
    _w(id='rifle-x', name='X-95 Assault Rifle', type='AUTOCANNON', round='5.56 mm', range_m=650, dispersion_mrad=1.6, muzzle_vel=920, pen_mm=9, damage=13, salvo=3, ammo=300, reload_s=0.3),
    _w(id='at-mpatgm', name='MPATGM Launcher', type='MISSILE', round='Tandem top-attack', range_m=2500, min_range_m=40, dispersion_mrad=0.3, muzzle_vel=240, pen_mm=800, damage=80, blast_m=4, ammo=3, reload_s=6, guided=True, top_attack=True, fx='MISSILE'),
    # Village Skirmish small arms: magazine (mag), magazine change (reload_mag_s), reload_s = time between shots,
    # auto = hold to fire, pellets = shotgun, zoom = sight magnification, head = headshot multiplier
    _w(id='pistol-p9', name='P9 Pistol', type='SMALLARMS', round='9 mm', range_m=120, dispersion_mrad=9, muzzle_vel=360, pen_mm=8, damage=24, ammo=45, mag=15, reload_s=0.22, reload_mag_s=1.6, head=2.0, zoom=1.3, model='Pistol'),
    _w(id='smg-v9', name='Vector-9 SMG', type='SMALLARMS', round='9 mm', range_m=160, dispersion_mrad=7, muzzle_vel=400, pen_mm=8, damage=19, ammo=120, mag=30, reload_s=0.075, reload_mag_s=2.0, auto=True, head=1.8, zoom=1.5, model='SMG'),
    _w(id='rifle-m4', name='M416 Carbine', type='SMALLARMS', round='5.56 mm', range_m=500, dispersion_mrad=2.6, muzzle_vel=880, pen_mm=12, damage=31, ammo=120, mag=30, reload_s=0.09, reload_mag_s=2.3, auto=True, head=2.1, zoom=2.0, model='AK'),
    _w(id='rifle-ak', name='AKM Rifle', type='SMALLARMS', round='7.62 mm', range_m=450, dispersion_mrad=3.4, muzzle_vel=715, pen_mm=14, damage=38, ammo=90, mag=30, reload_s=0.1, reload_mag_s=2.6, auto=True, head=2.1, zoom=1.8, model='AK'),
    _w(id='dmr-sks', name='SKS Marksman Rifle', type='SMALLARMS', round='7.62 mm', range_m=800, dispersion_mrad=0.9, muzzle_vel=760, pen_mm=16, damage=52, ammo=40, mag=10, reload_s=0.32, reload_mag_s=2.8, head=2.2, zoom=4.0, model='Sniper_2'),
    _w(id='sniper-awm', name='AWM Sniper Rifle', type='SMALLARMS', round='.300 Magnum', range_m=1000, dispersion_mrad=0.25, muzzle_vel=945, pen_mm=30, damage=110, ammo=15, mag=5, reload_s=1.5, reload_mag_s=3.5, head=2.5, zoom=8.0, model='Sniper'),
    _w(id='shotgun-s12', name='S12K Shotgun', type='SMALLARMS', round='12 gauge', range_m=45, dispersion_mrad=55, muzzle_vel=380, pen_mm=6, damage=13, pellets=9, ammo=32, mag=8, reload_s=0.55, reload_mag_s=3.0, head=1.5, zoom=1.3, model='Shotgun'),
    _w(id='at-rpg', name='RPG-7', type='SMALLARMS', round='HEAT rocket', range_m=400, dispersion_mrad=4, muzzle_vel=300, pen_mm=600, damage=95, blast_m=5, ammo=3, mag=1, reload_s=0.5, reload_mag_s=3.5, head=1.0, zoom=2.5, fx='ROCKET', model='RocketLauncher'),
    _w(id='glide-bomb', name='Precision Glide Bomb', type='MISSILE', round='Penetrator HE', range_m=14000, min_range_m=1500, dispersion_mrad=0.3, muzzle_vel=300, pen_mm=650, damage=120, blast_m=28, ammo=4, reload_s=9, guided=True, top_attack=True, fx='MISSILE'),
]


def _p(**kw) -> dict:
    base = dict(description='', swim_kph=0, ford_depth_m=1.2, max_grade_deg=30, turn_rate_dps=32, idle_burn_lph=20,
                crew_skill=0.75, stabilised=True, hp=100, signature=1.0, turret_dps=40, aps=None,
                point_defence=None, air=None, hover=False)
    base.update(kw)
    return base


def _pair(blue: dict, red_id: str, red_name: str, **tweak) -> list[dict]:
    b = _p(**blue)
    r = copy.deepcopy(b)
    r.update(tweak, id=red_id, name=red_name, faction='RED')
    return [b, r]


DEFAULT_PLATFORMS = [
    *_pair(dict(id='b-mbt-vajra', name='Vajra-X Railgun MBT', cls='MBT', faction='BLUE', model='mbt_rail',
                description='Next-gen MBT with an electromagnetic railgun, hard-kill APS and composite armour.',
                armour=dict(front=980, side=360, rear=120, top=95), dims=dict(length=9.8, width=3.7, height=2.5),
                mass_t=58, power_kw=1250, road_kph=72, offroad_kph=50, max_grade_deg=32, fuel_l=1600, fuel_l_per_km=4.6,
                sensor=dict(range_m=5200, thermal=True, radar=False), crew_skill=0.82, aps=dict(charges=4, p=0.72),
                weapons=['rail-120', 'atgm-top', 'hmg-127'], points=11, turret_dps=42),
           'r-mbt-typhoon', 'Typhoon-R Railgun MBT', crew_skill=0.78, power_kw=1300,
           armour=dict(front=940, side=380, rear=130, top=90)),
    *_pair(dict(id='b-mbt-bhishma', name='Bhishma-NG MBT', cls='MBT', faction='BLUE', model='mbt',
                description='Upgraded 125 mm smoothbore tank; the reliable workhorse.',
                armour=dict(front=820, side=300, rear=100, top=80), dims=dict(length=9.6, width=3.6, height=2.3),
                mass_t=48, power_kw=930, road_kph=65, offroad_kph=45, fuel_l=1200, fuel_l_per_km=4.0,
                sensor=dict(range_m=4500, thermal=True, radar=False), aps=dict(charges=2, p=0.6),
                weapons=['apfsds-125', 'heat-125', 'hmg-127'], points=8),
           'r-mbt-scorpion', 'Scorpion-K MBT'),
    *_pair(dict(id='b-ifv-garuda', name='Garuda Amphibious IFV', cls='IFV', faction='BLUE', model='ifv',
                description='Amphibious IFV with a 40 mm CT cannon and top-attack ATGMs.',
                armour=dict(front=260, side=90, rear=45, top=30), dims=dict(length=7.4, width=3.3, height=2.6),
                mass_t=26, power_kw=600, road_kph=75, offroad_kph=52, swim_kph=9, ford_depth_m=1.5, fuel_l=700,
                fuel_l_per_km=2.4, sensor=dict(range_m=4200, thermal=True, radar=False), weapons=['ac-40', 'atgm-top'],
                hp=80, points=6),
           'r-ifv-viper', 'Viper Amphibious IFV'),
    *_pair(dict(id='b-apc-kavach', name='Kavach Wheeled APC', cls='APC', faction='BLUE', model='apc',
                description='8×8 amphibious APC with remote weapon station.',
                armour=dict(front=110, side=45, rear=25, top=20), dims=dict(length=8.0, width=3.0, height=2.4),
                mass_t=20, power_kw=420, road_kph=100, offroad_kph=48, swim_kph=10, ford_depth_m=1.5, max_grade_deg=28,
                fuel_l=500, fuel_l_per_km=1.6, sensor=dict(range_m=3200, thermal=True, radar=False), weapons=['hmg-127'],
                hp=60, points=3, signature=0.85),
           'r-apc-jackal', 'Jackal Wheeled APC'),
    *_pair(dict(id='b-spg-trishul', name='Trishul Rail-SPG', cls='SPG', faction='BLUE', model='spg',
                description='Self-propelled hypersonic rail artillery: long reach, small footprint.',
                armour=dict(front=70, side=35, rear=25, top=25), dims=dict(length=10.5, width=3.4, height=3.1),
                mass_t=42, power_kw=750, road_kph=62, offroad_kph=38, max_grade_deg=26, fuel_l=900, fuel_l_per_km=3.2,
                sensor=dict(range_m=2500, thermal=True, radar=False), weapons=['rail-arty', 'hmg-127'], hp=70, points=8,
                turret_dps=18),
           'r-spg-thunder', 'Thunder 155 SPG', weapons=['arty-155', 'hmg-127']),
    *_pair(dict(id='b-mlrs-pinaka', name='Pinaka-NG MLRS', cls='MLRS', faction='BLUE', model='mlrs',
                description='Guided rocket launcher; six-round salvoes saturate a grid square.',
                armour=dict(front=40, side=25, rear=20, top=15), dims=dict(length=12, width=3.0, height=3.2),
                mass_t=30, power_kw=520, road_kph=85, offroad_kph=40, max_grade_deg=24, fuel_l=600, fuel_l_per_km=2.4,
                sensor=dict(range_m=2000, thermal=False, radar=False), weapons=['mlrs-214'], hp=55, points=8, turret_dps=20),
           'r-mlrs-storm', 'Storm MLRS'),
    *_pair(dict(id='b-rcn-netra', name='Netra Hover Recon', cls='RECON', faction='BLUE', model='recon',
                description='Fast hover recon craft with mast radar and long-range thermals. Crosses water.',
                armour=dict(front=45, side=22, rear=15, top=10), dims=dict(length=6.0, width=3.0, height=2.0),
                mass_t=12, power_kw=520, road_kph=115, offroad_kph=95, swim_kph=70, ford_depth_m=99, max_grade_deg=24,
                fuel_l=450, fuel_l_per_km=1.8, sensor=dict(range_m=9500, thermal=True, radar=True), weapons=['ac-30'],
                hp=50, points=5, signature=0.6, hover=True, crew_skill=0.85),
           'r-rcn-wraith', 'Wraith Hover Recon'),
    *_pair(dict(id='b-hov-agni', name='Agni Hover Tank', cls='HOVER', faction='BLUE', model='hover_tank',
                description='Ground-effect hover tank with a light railgun; amphibious and very fast.',
                armour=dict(front=420, side=150, rear=60, top=40), dims=dict(length=8.4, width=4.0, height=2.4),
                mass_t=30, power_kw=1100, road_kph=105, offroad_kph=88, swim_kph=75, ford_depth_m=99, max_grade_deg=22,
                fuel_l=900, fuel_l_per_km=3.8, sensor=dict(range_m=4800, thermal=True, radar=False),
                aps=dict(charges=3, p=0.65), weapons=['rail-90', 'hmg-127'], hp=85, points=9, hover=True),
           'r-hov-cobra', 'Cobra Hover Tank'),
    *_pair(dict(id='b-ad-akash', name='Akash-X Air Defence', cls='AD', faction='BLUE', model='ad',
                description='Radar SAM launcher with laser point-defence against missiles, shells and drones.',
                armour=dict(front=60, side=30, rear=20, top=15), dims=dict(length=8.5, width=3.2, height=3.4),
                mass_t=28, power_kw=520, road_kph=70, offroad_kph=40, max_grade_deg=26, fuel_l=600, fuel_l_per_km=2.4,
                sensor=dict(range_m=16000, thermal=True, radar=True), weapons=['sam-sr', 'laser-pd'], hp=60, points=7,
                point_defence=dict(range_m=3000, p=0.5)),
           'r-ad-spear', 'Spear Air Defence'),
    *_pair(dict(id='b-gs-rudra', name='Rudra-X Attack Gunship', cls='GUNSHIP', faction='BLUE', model='gunship',
                description='Tilt-rotor gunship: air-to-ground missiles and a chin cannon. Vulnerable to SAMs.',
                armour=dict(front=25, side=15, rear=10, top=10), dims=dict(length=15, width=13, height=4),
                mass_t=9, power_kw=3000, road_kph=290, offroad_kph=290, max_grade_deg=90, fuel_l=1400, fuel_l_per_km=3.0,
                sensor=dict(range_m=8000, thermal=True, radar=True), weapons=['agm-hf', 'ac-30'], hp=60, points=10,
                signature=1.4, air=dict(cruise_alt_m=140, hover=True), turret_dps=90, turn_rate_dps=25),
           'r-gs-hellcat', 'Hellcat Attack Gunship'),
    *_pair(dict(id='b-jet-tejas', name='Tejas-NG Strike Fighter', cls='JET', faction='BLUE', model='jet',
                description='Delta-wing strike fighter carrying precision glide bombs. Fast, short loiter.',
                armour=dict(front=15, side=10, rear=10, top=8), dims=dict(length=13.2, width=8.2, height=4.4),
                mass_t=13, power_kw=60000, road_kph=950, offroad_kph=950, max_grade_deg=90, fuel_l=2500, fuel_l_per_km=2.2,
                sensor=dict(range_m=12000, thermal=True, radar=True), weapons=['glide-bomb'], hp=70, points=12,
                signature=1.6, air=dict(cruise_alt_m=2200, hover=False), turret_dps=180, turn_rate_dps=12),
           'r-jet-falcon', 'Falcon Strike Fighter'),
    *_pair(dict(id='b-uav-drishti', name='Drishti Recon UAV', cls='UAV', faction='BLUE', model='uav',
                description='Long-endurance drone with EO/IR turret and two loitering munitions.',
                armour=dict(front=2, side=2, rear=2, top=2), dims=dict(length=6, width=12, height=1.6),
                mass_t=1.2, power_kw=120, road_kph=180, offroad_kph=180, max_grade_deg=90, fuel_l=300, fuel_l_per_km=0.25,
                sensor=dict(range_m=11000, thermal=True, radar=False), weapons=['atgm-lite'], hp=25, points=4,
                signature=0.35, air=dict(cruise_alt_m=1100, hover=False), crew_skill=0.85, turret_dps=120, turn_rate_dps=15),
           'r-uav-hawk', 'Hawk Recon UAV'),
    *_pair(dict(id='b-inf-trooper', name='Para SF Trooper', cls='INF', faction='BLUE', model='swat',
                description='Player soldier: assault rifle, man-portable top-attack missile, night-vision goggles. Can drive any friendly vehicle.',
                armour=dict(front=4, side=4, rear=3, top=3), dims=dict(length=0.6, width=0.6, height=1.8),
                mass_t=0.12, power_kw=1, road_kph=21, offroad_kph=19, swim_kph=3, ford_depth_m=99, max_grade_deg=50, turn_rate_dps=720,
                fuel_l=1, fuel_l_per_km=0, idle_burn_lph=0, sensor=dict(range_m=2500, thermal=True, radar=False), crew_skill=0.85,
                weapons=['rifle-x', 'at-mpatgm'], hp=100, points=1, signature=0.12, turret_dps=720),
           'r-inf-trooper', 'OPFOR Ranger'),
    # Village Skirmish vehicles (neutral until somebody drives them)
    _p(id='v-car', name='Hatchback', cls='CAR', faction='NEUTRAL', model='car', description='Civilian car: fast on the road, no armour.',
       armour=dict(front=3, side=3, rear=3, top=2), dims=dict(length=4.2, width=1.8, height=1.5), mass_t=1.2, power_kw=95,
       road_kph=120, offroad_kph=65, max_grade_deg=28, turn_rate_dps=95, fuel_l=45, fuel_l_per_km=0.07, idle_burn_lph=1,
       ford_depth_m=0.5, sensor=dict(range_m=600, thermal=False, radar=False), weapons=[], hp=260, points=0, signature=0.6, turret_dps=720),
    _p(id='v-bike', name='Motorbike', cls='CAR', faction='NEUTRAL', model='bike', description='Fastest way across the village.',
       armour=dict(front=1, side=1, rear=1, top=1), dims=dict(length=2.1, width=0.8, height=1.3), mass_t=0.2, power_kw=45,
       road_kph=135, offroad_kph=85, max_grade_deg=35, turn_rate_dps=150, fuel_l=15, fuel_l_per_km=0.03, idle_burn_lph=0.5,
       ford_depth_m=0.4, sensor=dict(range_m=600, thermal=False, radar=False), weapons=[], hp=140, points=0, signature=0.4, turret_dps=720),
    _p(id='v-jeep', name='Gypsy Patrol Jeep', cls='CAR', faction='NEUTRAL', model='jeep', description='Open patrol jeep with a 12.7 mm machine gun.',
       armour=dict(front=6, side=5, rear=4, top=2), dims=dict(length=4.2, width=1.9, height=1.9), mass_t=2.0, power_kw=130,
       road_kph=105, offroad_kph=75, max_grade_deg=32, turn_rate_dps=85, fuel_l=60, fuel_l_per_km=0.1, idle_burn_lph=1.5,
       ford_depth_m=0.8, sensor=dict(range_m=900, thermal=False, radar=False), weapons=['hmg-127'], hp=380, points=0, signature=0.7, turret_dps=180),
    _p(id='v-tank', name='Bhishma MBT', cls='MBT', faction='NEUTRAL', model='mbt', description='Abandoned main battle tank — bring an RPG.',
       armour=dict(front=780, side=300, rear=110, top=80), dims=dict(length=9.5, width=3.6, height=2.4), mass_t=48, power_kw=930,
       road_kph=60, offroad_kph=42, fuel_l=1200, fuel_l_per_km=4.0, sensor=dict(range_m=2000, thermal=True, radar=False),
       weapons=['heat-125', 'apfsds-125', 'hmg-127'], hp=900, points=0, signature=1.0, turret_dps=40),
    *_pair(dict(id='b-hvt-cp', name='Brigade Command Post (HVT)', cls='HVT', faction='BLUE', model='hvt',
                description='Hardened command, control and comms node. Loss of the HVT decides the battle.',
                armour=dict(front=250, side=250, rear=250, top=160), dims=dict(length=14, width=10, height=6),
                mass_t=200, power_kw=0, road_kph=0, offroad_kph=0, max_grade_deg=0, fuel_l=1, fuel_l_per_km=0,
                sensor=dict(range_m=3500, thermal=True, radar=True), weapons=['laser-pd'], hp=700, points=0,
                signature=1.2, point_defence=dict(range_m=2500, p=0.35), idle_burn_lph=0),
           'r-hvt-cp', 'Corps Forward HQ (HVT)'),
]

ROLE_IDS = {
    'BLUE': {'inf': 'b-inf-trooper', 'mbt-rail': 'b-mbt-vajra', 'mbt-conv': 'b-mbt-bhishma', 'ifv': 'b-ifv-garuda', 'apc': 'b-apc-kavach',
             'spg': 'b-spg-trishul', 'mlrs': 'b-mlrs-pinaka', 'recon': 'b-rcn-netra', 'hover': 'b-hov-agni',
             'ad': 'b-ad-akash', 'gunship': 'b-gs-rudra', 'jet': 'b-jet-tejas', 'uav': 'b-uav-drishti', 'hvt': 'b-hvt-cp'},
    'RED': {'inf': 'r-inf-trooper', 'mbt-rail': 'r-mbt-typhoon', 'mbt-conv': 'r-mbt-scorpion', 'ifv': 'r-ifv-viper', 'apc': 'r-apc-jackal',
            'spg': 'r-spg-thunder', 'mlrs': 'r-mlrs-storm', 'recon': 'r-rcn-wraith', 'hover': 'r-hov-cobra',
            'ad': 'r-ad-spear', 'gunship': 'r-gs-hellcat', 'jet': 'r-jet-falcon', 'uav': 'r-uav-hawk', 'hvt': 'r-hvt-cp'},
}

FORMATION_IDS = ['ALPHA', 'BRAVO', 'CHARLIE', 'DELTA']
FORMATION_ROLE = {'ALPHA': 'Assault', 'BRAVO': 'HVT Defence', 'CHARLIE': 'Fire Support', 'DELTA': 'Reconnaissance'}
DOCTRINES = ['AGGRESSIVE', 'FLANKING', 'SUPPRESSIVE', 'DEFENSIVE', 'RECON', 'HOLD']
SHAPES = ['WEDGE', 'LINE', 'COLUMN', 'ECHELON_L', 'ECHELON_R', 'VEE', 'RING', 'DIAMOND']

_OOB = [
    ('ALPHA', 'AGGRESSIVE', 'WEDGE', {'mbt-rail': 4, 'mbt-conv': 2, 'ifv': 2, 'gunship': 1}),
    ('BRAVO', 'DEFENSIVE', 'RING', {'mbt-conv': 1, 'ifv': 2, 'apc': 2, 'ad': 1}),
    ('CHARLIE', 'SUPPRESSIVE', 'LINE', {'spg': 3, 'mlrs': 1, 'apc': 1, 'jet': 1}),
    ('DELTA', 'RECON', 'LINE', {'recon': 2, 'hover': 2, 'uav': 1}),
]


def hvt_platform_for(side: str) -> str:
    return ROLE_IDS[side]['hvt']


def default_order_of_battle(side: str) -> list[dict]:
    return [dict(id=fid, doctrine=doc, shape=shape,
                 composition={ROLE_IDS[side][role]: n for role, n in comp.items()})
            for fid, doc, shape, comp in _OOB]


def default_inventory(side: str) -> dict:
    """Default allowance = 1.5× the default order of battle, rounded up."""
    inv: dict = {}
    for f in default_order_of_battle(side):
        for pid, n in f['composition'].items():
            inv[pid] = inv.get(pid, 0) + n
    return {k: math.ceil(v * 1.5) for k, v in inv.items()}


def catalogue() -> dict:
    """Default catalogue snapshot {platforms: {id: spec}, weapons: {id: spec}}."""
    return {'platforms': {p['id']: copy.deepcopy(p) for p in DEFAULT_PLATFORMS},
            'weapons': {w['id']: copy.deepcopy(w) for w in DEFAULT_WEAPONS}}
