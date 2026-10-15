"""Domain services shared by the REST API, the WebSocket consumer and management commands."""
from __future__ import annotations

import random
import secrets
import string
import threading

from django.conf import settings
from django.db import transaction

from sim.inventory import catalogue as default_catalogue, default_inventory
from sim.pathfind import CORRIDOR_WIDTH_M, NavGrid, generate_corridors
from sim.terrain import TerrainGrid as Grid
from sim.terrarium import flat_terrain, ingest_terrarium
from sim.theatres import default_zones, theatre_by_id

from .models import ForceKey, Platform, Scenario, TerrainGrid, Weapon, Zone

_terrain_cache: dict = {}
_cache_lock = threading.Lock()


class TerrainBundle:
    def __init__(self, grid: Grid, corridors: list):
        self.grid = grid
        self.corridors = corridors
        grid.set_corridors([c['path'] for c in corridors], grid.meta.get('road_width', CORRIDOR_WIDTH_M))
        self.nav = NavGrid(grid)
        self.stats = grid.stats()


def room_code() -> str:
    alphabet = string.ascii_uppercase.replace('O', '').replace('I', '') + '23456789'
    while True:
        code = ''.join(secrets.choice(alphabet) for _ in range(6))
        if not Scenario.objects.filter(room_code=code).exists():
            return code


def zones_of(scenario: Scenario) -> dict:
    return {z.side: z.polygon for z in scenario.zones.all()}


def ensure_default_zones(scenario: Scenario):
    if scenario.mode == 'SKIRMISH':
        return  # spawn areas come with the village map
    t = theatre_by_id(scenario.theatre)
    dz = default_zones(t, scenario.ao_half_km)
    for side in ('BLUE', 'RED'):
        Zone.objects.get_or_create(scenario=scenario, side=side, defaults={'polygon': dz[side], 'method': 'default'})


def load_terrain(scenario: Scenario) -> TerrainBundle | None:
    with _cache_lock:
        b = _terrain_cache.get(scenario.id)
    if b is not None:
        return b
    row = TerrainGrid.objects.filter(scenario=scenario).first()
    if row is None:
        return None
    grid = Grid.from_bytes(row.meta, bytes(row.elev))
    b = TerrainBundle(grid, row.corridors or [])
    with _cache_lock:
        _terrain_cache[scenario.id] = b
    return b


def drop_terrain_cache(scenario_id: str):
    with _cache_lock:
        _terrain_cache.pop(scenario_id, None)


def ingest_village(scenario: Scenario, seed: int | None = None) -> dict:
    """Village Skirmish map: a fresh procedural village (new seed each time unless given)."""
    from sim import village
    t = theatre_by_id(scenario.theatre)
    seed = seed if seed is not None else random.randrange(1, 2 ** 31)
    lay = village.generate(seed, base_elev=round(t.flat_elevation, 1))
    grid = Grid(village.village_meta(lay, t.biome, scenario.origin_lat, scenario.origin_lon), village.terrain_elevation(lay))
    corridors = village.road_corridors(lay)
    kinds = {}
    for b in lay['buildings']:
        kinds[b['k']] = kinds.get(b['k'], 0) + 1
    note = (f"Village Skirmish map #{seed}: {len(lay['buildings'])} buildings ({', '.join(f'{n} {k}' for k, n in kinds.items())}), "
            f"lake, {len(lay['vehicles'])} vehicles, {len(lay['loot'])} loot spots")
    TerrainGrid.objects.update_or_create(scenario=scenario, defaults=dict(
        meta=grid.meta, stats=grid.stats(), corridors=corridors, elev=grid.elev_bytes(), source_note=note))
    # spawn areas are the zones
    for side in ('BLUE', 'RED'):
        sx, sy = lay['spawns'][side][0]
        poly = [[sx - 45, sy - 45], [sx + 45, sy - 45], [sx + 45, sy + 45], [sx - 45, sy + 45]]
        Zone.objects.update_or_create(scenario=scenario, side=side, defaults={'polygon': poly, 'method': 'spawn'})
    drop_terrain_cache(scenario.id)
    b = load_terrain(scenario)
    return dict(fallback=False, note=note, stats=b.stats, meta={k: v for k, v in grid.meta.items() if k != 'village'},
                corridors=corridors)


def create_skirmish(owner, theatre: str = 'punjab-asal-uttar', name: str | None = None, **opts) -> Scenario:
    """A ready-to-open Village Skirmish scenario: 10 v 10 (bots fill), 15 minutes, 10 ticks/s."""
    t = theatre_by_id(theatre)
    s = Scenario.objects.create(
        owner=owner, name=name or f'Village Skirmish — {t.name}', mode='SKIRMISH', theatre=t.id,
        description='Close-quarters squad battle in a village: loot weapons, drive cars, bikes and tanks, last squad standing wins.',
        time_of_day=opts.get('time_of_day', 'DAY'), weather=opts.get('weather', 'CLEAR'), tick_s=0.1, cadence_ms=100,
        max_ticks=int(opts.get('duration_min', 15) * 600), ao_half_km=0.8, cell_m=2.0, red_mode='MIXED', fog_enabled=True,
        advisor_every=100000, room_code=room_code(), origin_lat=t.lat, origin_lon=t.lon)
    ingest_village(s)
    return s


def ingest_terrain(scenario: Scenario, mode: str = 'real') -> dict:
    """Ingest real elevation (TR-3) or build the flat fallback (TR-5); regenerate corridors."""
    if scenario.mode == 'SKIRMISH':
        return ingest_village(scenario)
    t = theatre_by_id(scenario.theatre)
    note = ''
    fallback = False
    if mode == 'flat':
        grid = flat_terrain(scenario.origin_lat, scenario.origin_lon, scenario.ao_half_km, scenario.cell_m, t)
        fallback, note = True, 'Flat, unobstructed going (selected by administrator)'
    else:
        try:
            grid = ingest_terrarium(scenario.origin_lat, scenario.origin_lon, scenario.ao_half_km, scenario.cell_m, t,
                                    settings.WARGAME['TILE_CACHE'], settings.WARGAME['OFFLINE_TERRAIN'])
            note = f'AWS Terrain Tiles (terrarium) zoom {grid.meta.get("zoom")}'
        except Exception as e:  # network down / tiles missing → flat fallback, the drill can still run
            grid = flat_terrain(scenario.origin_lat, scenario.origin_lon, scenario.ao_half_km, scenario.cell_m, t)
            fallback, note = True, f'Fallback to flat terrain: {e}'
    corridors = generate_corridors(grid, zones_of(scenario))
    TerrainGrid.objects.update_or_create(scenario=scenario, defaults=dict(
        meta=grid.meta, stats=grid.stats(), corridors=corridors, elev=grid.elev_bytes(), source_note=note))
    drop_terrain_cache(scenario.id)
    b = load_terrain(scenario)
    return dict(fallback=fallback, note=note, stats=b.stats, meta=grid.meta, corridors=corridors)


def refresh_corridors(scenario: Scenario) -> list | None:
    b = load_terrain(scenario)
    if b is None:
        return None
    corridors = generate_corridors(b.grid, zones_of(scenario))
    TerrainGrid.objects.filter(scenario=scenario).update(corridors=corridors)
    drop_terrain_cache(scenario.id)
    return corridors


# ── catalogue (PW-1) ────────────────────────────────────────────────────────

def seed_assets(reset: bool = False):
    cat = default_catalogue()
    with transaction.atomic():
        if reset:
            Platform.objects.all().delete()
            Weapon.objects.all().delete()
        for pid, p in cat['platforms'].items():
            Platform.objects.get_or_create(id=pid, defaults={'data': p})
        for wid, w in cat['weapons'].items():
            Weapon.objects.get_or_create(id=wid, defaults={'data': w})


def load_catalogue() -> dict:
    """Snapshot of the editable catalogue (falls back to defaults if the DB is empty)."""
    plats = {p.id: dict(p.data, id=p.id) for p in Platform.objects.all()}
    weps = {w.id: dict(w.data, id=w.id) for w in Weapon.objects.all()}
    if not plats or not weps:
        return default_catalogue()
    # built-in entries added by newer releases (e.g. player soldiers) are available without a re-seed
    base = default_catalogue()
    for k, v in base['platforms'].items():
        plats.setdefault(k, v)
    for k, v in base['weapons'].items():
        weps.setdefault(k, v)
    return {'platforms': plats, 'weapons': weps}


def inventory_for(scenario: Scenario, side: str) -> dict | None:
    """Allowed roster for a side; None = unrestricted order of battle."""
    inv = scenario.inventory or {}
    if side not in inv:
        return default_inventory(side)
    return inv[side]


# ── force keys & readiness ─────────────────────────────────────────────────

def generate_keys(scenario: Scenario) -> dict:
    ForceKey.objects.filter(scenario=scenario).update(revoked=True)
    out = {}
    for side in ('BLUE', 'RED'):
        key = f'{side[:3]}-{scenario.room_code}-{secrets.token_hex(3).upper()}'
        ForceKey.objects.create(key=key, scenario=scenario, side=side)
        out[side] = key
    return out


def active_keys(scenario: Scenario) -> dict:
    return {k.side: k.key for k in scenario.keys.filter(revoked=False)}


def readiness(scenario: Scenario) -> dict:
    """Pre-battle checklist (AD-3 Readiness tab)."""
    z = zones_of(scenario)
    terrain = TerrainGrid.objects.filter(scenario=scenario).first()
    keys = active_keys(scenario)
    from .rooms import registry
    room = registry.peek(scenario.room_code)
    lobby = room.lobby_state('ADMIN') if room else None
    checks = [
        dict(id='terrain', label='Terrain ingested', ok=terrain is not None,
             detail=(terrain.source_note if terrain else 'Ingest terrain on the Terrain tab')),
        dict(id='zones', label='BLUE and RED zones drawn', ok=len(z.get('BLUE', [])) >= 3 and len(z.get('RED', [])) >= 3,
             detail=f"BLUE {len(z.get('BLUE', []))} vertices, RED {len(z.get('RED', []))} vertices"),
        dict(id='corridors', label='Mobility corridors generated', ok=bool(terrain and terrain.corridors),
             detail=f"{len(terrain.corridors) if terrain else 0} avenues of approach"),
        dict(id='keys', label='Force keys generated', ok='BLUE' in keys and 'RED' in keys,
             detail=f'RED mode: {scenario.get_red_mode_display()}'),
        dict(id='blue_ready', label='BLUE force ready', ok=bool(lobby and lobby['ready']['BLUE']),
             detail='Commander marks Ready after deploying formations and the HVT'),
        dict(id='red_ready', label='RED force ready', ok=bool(lobby and lobby['ready']['RED']) or scenario.red_mode == 'AI',
             detail='AI Red auto-generates and readies' if scenario.red_mode == 'AI' else 'RED commander marks Ready'),
    ]
    return dict(checks=checks, can_open=all(c['ok'] for c in checks[:4]), lobby=lobby)
