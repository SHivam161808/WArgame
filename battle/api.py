"""Documented REST API (SA-3). OpenAPI: /api/schema/ · Swagger UI: /api/docs/.

Administrators authenticate with their Django session (+ CSRF). Players get a token from
POST /api/join/ and send it as ``X-Player-Token`` to read their battle's after-action data.
"""
from __future__ import annotations

import hashlib
import json
import math
import secrets
import zlib

from django.contrib.auth import authenticate, login, logout
from django.db import IntegrityError, transaction
from django.db.models import Count
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from sim import advisor
from sim.engine import filter_frame
from sim.geo import grid_ref, to_geo
from sim.inventory import (AIR_CLASSES, CLASS_LABEL, DOCTRINES, FORMATION_IDS, FORMATION_ROLE, PLATFORM_CLASSES, SHAPES,
                           WEAPON_TYPES, catalogue as default_catalogue)
from sim.physics import GROUND
from sim.theatres import THEATRES, TIME_OF_DAY, WEATHER, theatre_by_id

from . import services
from .models import (AdvisorLog, Battle, BattleEvent, Casualty, Entity, ForceKey, HVT, OrbatNode, Platform, Player,
                     Scenario, TerrainGrid, Tick, Weapon, Zone)
from .rooms import RoomError, registry


def doc(**kw):
    """OpenAPI metadata for function views: free-form JSON bodies documented by summary."""
    kw.setdefault('request', OpenApiTypes.OBJECT)
    kw.setdefault('responses', OpenApiTypes.OBJECT)
    return extend_schema(**kw)


class IsStaff(permissions.BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_staff)


def err(msg: str, code=400):
    return Response({'error': msg}, status=code)


def own_scenario(request, sid: str) -> Scenario:
    qs = Scenario.objects.all() if request.user.is_superuser else Scenario.objects.filter(owner=request.user)
    return get_object_or_404(qs, id=sid)


def player_from_request(request) -> Player | None:
    tok = request.headers.get('X-Player-Token') or request.query_params.get('token')
    if not tok:
        return None
    return Player.objects.filter(token_hash=hashlib.sha256(tok.encode()).hexdigest()).first()


def battle_access(request, bid: str) -> tuple[Battle, str]:
    """Return (battle, audience). Owners see everything; players see their battle after it ends."""
    b = get_object_or_404(Battle.objects.select_related('scenario'), id=bid)
    u = request.user
    if u.is_authenticated and u.is_staff and (b.scenario.owner_id == u.id or u.is_superuser):
        return b, 'ADMIN'
    p = player_from_request(request)
    if p and p.scenario_id == b.scenario_id:
        return b, ('ADMIN' if b.status == 'ENDED' else p.side)
    raise PermissionError


def access_or_403(fn):
    def wrap(request, *a, **kw):
        try:
            return fn(request, *a, **kw)
        except PermissionError:
            return err('not allowed', 403)
    wrap.__name__ = fn.__name__
    wrap.__doc__ = fn.__doc__
    return wrap


# ── auth ───────────────────────────────────────────────────────────────────

@doc(tags=['auth'], summary='Administrator login (session cookie)')
@api_view(['POST'])
@permission_classes([permissions.AllowAny])
def auth_login(request):
    user = authenticate(request, username=request.data.get('username', ''), password=request.data.get('password', ''))
    if user is None or not user.is_staff:
        return err('invalid credentials', 401)
    login(request, user)
    return Response({'username': user.username})


@doc(tags=['auth'], summary='Logout')
@api_view(['POST'])
def auth_logout(request):
    logout(request)
    return Response({'ok': True})


@doc(tags=['auth'], summary='Current user')
@api_view(['GET'])
@permission_classes([permissions.AllowAny])
def auth_me(request):
    u = request.user
    return Response({'authenticated': u.is_authenticated, 'username': u.username if u.is_authenticated else None,
                     'is_staff': bool(u.is_authenticated and u.is_staff)})


# ── reference data ─────────────────────────────────────────────────────────

@doc(tags=['meta'], summary='Theatres, weather, times of day, doctrines, shapes and classes')
@api_view(['GET'])
@permission_classes([permissions.AllowAny])
def meta(request):
    return Response(dict(theatres=[t.to_dict() for t in THEATRES], weather=WEATHER, time_of_day=TIME_OF_DAY,
                         doctrines=DOCTRINES, shapes=SHAPES, formations=[dict(id=f, role=FORMATION_ROLE[f]) for f in FORMATION_IDS],
                         classes=[dict(id=c, label=CLASS_LABEL[c], air=c in AIR_CLASSES) for c in PLATFORM_CLASSES],
                         weapon_types=WEAPON_TYPES, ground=GROUND))


# ── scenarios (AD-1, AD-2) ─────────────────────────────────────────────────

SCENARIO_FIELDS = ['name', 'description', 'theatre', 'time_of_day', 'weather', 'tick_s', 'cadence_ms', 'max_ticks',
                   'ao_half_km', 'cell_m', 'red_mode', 'fog_enabled', 'advisor_every', 'allow_ai_blue']


def scenario_json(s: Scenario, detail: bool = False) -> dict:
    last = s.battles.order_by('-run_no').first()
    d = dict(id=s.id, name=s.name, description=s.description, theatre=s.theatre, theatre_name=theatre_by_id(s.theatre).name,
             time_of_day=s.time_of_day, weather=s.weather, tick_s=s.tick_s, cadence_ms=s.cadence_ms, max_ticks=s.max_ticks,
             duration_min=round(s.duration_s / 60, 1), ao_half_km=s.ao_half_km, cell_m=s.cell_m, room_code=s.room_code,
             red_mode=s.red_mode, mode=s.mode, fog_enabled=s.fog_enabled, advisor_every=s.advisor_every, allow_ai_blue=s.allow_ai_blue,
             origin=dict(lat=s.origin_lat, lon=s.origin_lon), status=s.status, created=s.created, updated=s.updated,
             battle=dict(id=last.id, run_no=last.run_no, status=last.status, tick=last.current_tick, outcome=last.outcome,
                         progress=round(min(1.0, last.current_tick / max(1, s.max_ticks)) * 100, 1)) if last else None)
    if detail:
        d['inventory'] = {side: services.inventory_for(s, side) for side in ('BLUE', 'RED')}
        d['zones'] = services.zones_of(s)
        d['keys'] = services.active_keys(s)
        d['briefing'] = s.briefing
        t = TerrainGrid.objects.filter(scenario=s).first()
        d['terrain'] = dict(meta={k: v for k, v in t.meta.items() if k != 'village'}, stats=t.stats, corridors=t.corridors,
                            note=t.source_note) if t else None
    return d


def _clean_scenario(data: dict, partial: bool) -> dict:
    out = {}
    for k in SCENARIO_FIELDS:
        if k in data:
            out[k] = data[k]
    if 'theatre' in out and out['theatre'] not in {t.id for t in THEATRES}:
        raise ValueError('unknown theatre')
    if 'time_of_day' in out and out['time_of_day'] not in TIME_OF_DAY:
        raise ValueError('unknown time of day')
    if 'weather' in out and out['weather'] not in WEATHER:
        raise ValueError('unknown weather')
    if 'red_mode' in out and out['red_mode'] not in ('AI', 'HUMAN', 'MIXED'):
        raise ValueError('red_mode must be AI, HUMAN or MIXED')
    for k, lo, hi in (('tick_s', 0.05, 10), ('cadence_ms', 20, 10000), ('max_ticks', 10, 500000), ('ao_half_km', 5, 40),
                      ('cell_m', 25, 200), ('advisor_every', 10, 100000)):
        if k in out:
            v = float(out[k])
            if not lo <= v <= hi:
                raise ValueError(f'{k} must be between {lo} and {hi}')
            out[k] = v if k in ('tick_s', 'ao_half_km', 'cell_m') else int(v)
    if 'duration_min' in data and 'tick_s' in (out or data):
        tick = float(out.get('tick_s', data.get('tick_s', 0.25)))
        out['max_ticks'] = int(float(data['duration_min']) * 60 / tick)
    if not partial and not out.get('name'):
        raise ValueError('name is required')
    return out


@doc(tags=['scenarios'], summary='List own scenarios / create a scenario')
@api_view(['GET', 'POST'])
@permission_classes([IsStaff])
def scenarios(request):
    if request.method == 'GET':
        qs = Scenario.objects.all() if request.user.is_superuser else Scenario.objects.filter(owner=request.user)
        return Response([scenario_json(s) for s in qs])
    try:
        data = _clean_scenario(request.data, partial=False)
    except (ValueError, TypeError) as e:
        return err(str(e))
    data.setdefault('theatre', THEATRES[0].id)
    t = theatre_by_id(data['theatre'])
    data.setdefault('weather', t.default_weather)
    s = Scenario.objects.create(owner=request.user, room_code=services.room_code(), origin_lat=t.lat, origin_lon=t.lon, **data)
    services.ensure_default_zones(s)
    return Response(scenario_json(s, True), status=201)


@doc(tags=['scenarios'], summary='Get / update / delete a scenario')
@api_view(['GET', 'PATCH', 'DELETE'])
@permission_classes([IsStaff])
def scenario_detail(request, sid):
    s = own_scenario(request, sid)
    if request.method == 'GET':
        return Response(scenario_json(s, True))
    if request.method == 'DELETE':
        registry.close(s.room_code)
        s.delete()
        return Response(status=204)
    if s.status == 'RUNNING':
        return err('stop the battle before editing the scenario', 409)
    try:
        data = _clean_scenario(request.data, partial=True)
    except (ValueError, TypeError) as e:
        return err(str(e))
    theatre_changed = 'theatre' in data and data['theatre'] != s.theatre
    for k, v in data.items():
        setattr(s, k, v)
    if theatre_changed:
        t = theatre_by_id(s.theatre)
        s.origin_lat, s.origin_lon = t.lat, t.lon
        s.zones.all().delete()
        TerrainGrid.objects.filter(scenario=s).delete()
        services.drop_terrain_cache(s.id)
    s.save()
    if theatre_changed:
        services.ensure_default_zones(s)
    return Response(scenario_json(s, True))


# ── terrain (TR-1..7) ──────────────────────────────────────────────────────

@doc(tags=['terrain'], summary='Ingest real elevation (mode=real) or the flat fallback (mode=flat)')
@api_view(['POST'])
@permission_classes([IsStaff])
def terrain_ingest(request, sid):
    s = own_scenario(request, sid)
    if s.status == 'RUNNING':
        return err('battle running', 409)
    out = services.ingest_terrain(s, request.data.get('mode', 'real'))
    return Response(out)


@doc(tags=['terrain'], summary='Terrain metadata, statistics and mobility corridors')
@api_view(['GET'])
@permission_classes([permissions.AllowAny])
def terrain_info(request, sid):
    s = get_object_or_404(Scenario, id=sid)
    t = TerrainGrid.objects.filter(scenario=s).first()
    if t is None:
        return err('terrain not ingested', 404)
    return Response(dict(meta=t.meta, stats=t.stats, corridors=t.corridors, note=t.source_note))


@doc(tags=['terrain'], summary='PNG preview of a terrain layer (relief | ground | slope)')
@api_view(['GET'])
@permission_classes([permissions.AllowAny])
def terrain_layer(request, sid):
    s = get_object_or_404(Scenario, id=sid)
    b = services.load_terrain(s)
    if b is None:
        return err('terrain not ingested', 404)
    layer = request.query_params.get('layer', 'relief')
    return HttpResponse(b.grid.layer_png(layer if layer in ('relief', 'ground', 'slope') else 'relief'), content_type='image/png')


@doc(tags=['terrain'], summary='Terrain at a point: elevation, slope, class, ground, mobility, concealment, LOS')
@api_view(['GET'])
@permission_classes([permissions.AllowAny])
def terrain_sample(request, sid):
    s = get_object_or_404(Scenario, id=sid)
    b = services.load_terrain(s)
    if b is None:
        return err('terrain not ingested', 404)
    g = b.grid
    x, y = float(request.query_params['x']), float(request.query_params['y'])
    out = dict(x=x, y=y, elevation=round(g.height(x, y), 1), slope_deg=round(g.slope_at(x, y), 1), terrain_class=g.class_at(x, y),
               ground=g.ground_at(x, y), mobility=round(g.mobility_at(x, y), 2), concealment=round(g.conceal_at(x, y), 2),
               water_depth=round(g.water_depth(x, y), 2), on_corridor=g.on_corridor(x, y), grid=grid_ref(x, y, g.half_m))
    if 'x2' in request.query_params:
        x2, y2 = float(request.query_params['x2']), float(request.query_params['y2'])
        out['los'] = g.los(x, y, g.height(x, y) + 2.5, x2, y2, g.height(x2, y2) + 2.5)
        out['range_m'] = round(math.hypot(x2 - x, y2 - y))
    return Response(out)


# ── zones (TR-2) ───────────────────────────────────────────────────────────

@doc(tags=['zones'], summary='Get / draw (PUT) / erase (DELETE) a deployment zone; side = BLUE | RED')
@api_view(['GET', 'PUT', 'DELETE'])
@permission_classes([IsStaff])
def zone(request, sid, side):
    s = own_scenario(request, sid)
    side = side.upper()
    if side not in ('BLUE', 'RED'):
        return err('side must be BLUE or RED')
    if request.method == 'GET':
        z = s.zones.filter(side=side).first()
        return Response(dict(side=side, polygon=z.polygon if z else None, method=z.method if z else None))
    if s.status == 'RUNNING':
        return err('battle running', 409)
    if request.method == 'DELETE':
        s.zones.filter(side=side).delete()
        return Response(status=204)
    poly = request.data.get('polygon')
    try:
        poly = [[round(float(p[0]), 1), round(float(p[1]), 1)] for p in poly]
    except (TypeError, ValueError, IndexError):
        return err('polygon must be a list of [x, y] points')
    if not 3 <= len(poly) <= 500:
        return err('a zone needs 3–500 vertices')
    lim = s.ao_half_km * 1000
    if any(abs(x) > lim or abs(y) > lim for x, y in poly):
        return err('zone must lie inside the area of operations')
    Zone.objects.update_or_create(scenario=s, side=side, defaults=dict(polygon=poly, method=request.data.get('method', 'vertices')[:10]))
    corridors = services.refresh_corridors(s)
    return Response(dict(side=side, polygon=poly, corridors=corridors))


# ── inventory, keys, readiness, lobby (AD-3) ───────────────────────────────

@doc(tags=['scenarios'], summary='Allowed roster per force ({BLUE: {platform: n} | null, RED: …}); null = unrestricted')
@api_view(['GET', 'PUT'])
@permission_classes([IsStaff])
def inventory(request, sid):
    s = own_scenario(request, sid)
    if request.method == 'GET':
        return Response({side: services.inventory_for(s, side) for side in ('BLUE', 'RED')})
    plats = set(Platform.objects.values_list('id', flat=True)) or set(default_catalogue()['platforms'])
    inv = {}
    for side in ('BLUE', 'RED'):
        v = request.data.get(side, services.inventory_for(s, side))
        if v is None:
            inv[side] = None
            continue
        try:
            inv[side] = {k: int(n) for k, n in v.items() if k in plats and int(n) > 0}
        except (TypeError, ValueError, AttributeError):
            return err(f'bad inventory for {side}')
    s.inventory = inv
    s.save(update_fields=['inventory', 'updated'])
    return Response(inv)


@doc(tags=['scenarios'], summary='Generate fresh force keys (revokes old ones) / list keys')
@api_view(['GET', 'POST'])
@permission_classes([IsStaff])
def keys(request, sid):
    s = own_scenario(request, sid)
    if request.method == 'POST':
        if 'red_mode' in request.data:
            if request.data['red_mode'] not in ('AI', 'HUMAN', 'MIXED'):
                return err('bad red_mode')
            s.red_mode = request.data['red_mode']
            s.save(update_fields=['red_mode', 'updated'])
        return Response(dict(keys=services.generate_keys(s), room_code=s.room_code, red_mode=s.red_mode))
    return Response(dict(keys=services.active_keys(s), room_code=s.room_code, red_mode=s.red_mode))


@doc(tags=['scenarios'], summary='Readiness checklist')
@api_view(['GET'])
@permission_classes([IsStaff])
def readiness(request, sid):
    return Response(services.readiness(own_scenario(request, sid)))


@doc(tags=['scenarios'], summary='Open the lobby (creates a battle run; players can join with force keys)')
@api_view(['POST'])
@permission_classes([IsStaff])
def open_lobby(request, sid):
    s = own_scenario(request, sid)
    r = services.readiness(s)
    if not r['can_open']:
        return err('readiness checks failed: ' + ', '.join(c['label'] for c in r['checks'][:4] if not c['ok']), 409)
    s.status = 'LOBBY'
    s.save(update_fields=['status', 'updated'])
    room = registry.open(s, new_run=bool(request.data.get('new_run', True)))
    return Response(dict(room_code=s.room_code, battle_id=room.row.id, briefing=room.briefing))


@doc(tags=['scenarios'], summary='Close the room (stops a running battle)')
@api_view(['POST'])
@permission_classes([IsStaff])
def close_lobby(request, sid):
    s = own_scenario(request, sid)
    room = registry.peek(s.room_code)
    if room and room.sim.status in ('RUNNING', 'PAUSED'):
        room.control(None, 'end')
    registry.close(s.room_code)
    if s.status != 'ENDED':
        s.status = 'SETUP'
        s.save(update_fields=['status', 'updated'])
    return Response({'ok': True})


@doc(tags=['scenarios'], summary='AI-generated mission briefing (AI-6)')
@api_view(['POST'])
@permission_classes([IsStaff])
def briefing(request, sid):
    s = own_scenario(request, sid)
    b = services.load_terrain(s)
    from sim.engine import Battle as SimBattle
    plans = {}
    if b is not None:
        sim = SimBattle('preview', s.config(), b.grid, b.corridors, services.zones_of(s), services.load_catalogue(), 1, nav=b.nav)
        plans = {side: sim.auto_plan(side, services.inventory_for(s, side)) for side in ('BLUE', 'RED')}
    text = advisor.briefing(theatre_by_id(s.theatre), s.config(), services.zones_of(s), b.stats if b else None,
                            b.corridors if b else [], plans, services.load_catalogue()['platforms'])
    s.briefing = text
    s.save(update_fields=['briefing', 'updated'])
    return Response(dict(briefing=text))


# ── join (PL-4, AC-2) ──────────────────────────────────────────────────────

@doc(tags=['play'], summary='Join a room with Room ID + force key; returns a player token')
@api_view(['POST'])
@permission_classes([permissions.AllowAny])
@throttle_classes([ScopedRateThrottle])
def join(request):
    code = str(request.data.get('room_code', '')).strip().upper()
    key = str(request.data.get('force_key', '')).strip().upper()
    callsign = str(request.data.get('callsign', '')).strip()[:24] or 'Commander'
    fk = ForceKey.objects.select_related('scenario').filter(key=key, revoked=False, scenario__room_code=code).first()
    if fk is None:
        return err('Room ID or force key is not valid', 403)
    s = fk.scenario
    if s.status == 'SETUP':
        return err('The Exercise Controller has not opened this room yet', 409)
    if fk.side == 'RED' and s.red_mode == 'AI':
        return err('RED is played by the AI in this scenario', 409)
    room = registry.get(code)
    if room is None:
        return err('room not available', 409)
    if len(room.side_players(fk.side)) >= 4:
        return err('This force already has 4 players', 409)
    token = secrets.token_urlsafe(24)
    p = Player.objects.create(scenario=s, side=fk.side, callsign=callsign, token_hash=hashlib.sha256(token.encode()).hexdigest())
    return Response(dict(token=token, player_id=p.id, side=fk.side, room_code=code, battle_id=room.row.id))


join.cls.throttle_scope = 'join'  # ScopedRateThrottle reads the view's scope


# ── battles, ticks, analytics (n, AR-1..3) ─────────────────────────────────

@doc(tags=['battles'], summary='Battle runs of a scenario')
@api_view(['GET'])
@permission_classes([IsStaff])
def scenario_battles(request, sid):
    s = own_scenario(request, sid)
    return Response([dict(id=b.id, run_no=b.run_no, status=b.status, outcome=b.outcome, reason=b.reason, tick=b.current_tick,
                          started=b.started, ended=b.ended) for b in s.battles.all()])


@doc(tags=['battles'], summary='Battle summary, result and scenario context')
@api_view(['GET'])
@permission_classes([permissions.AllowAny])
@access_or_403
def battle_detail(request, bid):
    b, aud = battle_access(request, bid)
    s = b.scenario
    t = TerrainGrid.objects.filter(scenario=s).first()
    last = Tick.objects.filter(battle=b).order_by('-tick').values_list('tick', flat=True).first()
    return Response(dict(id=b.id, run_no=b.run_no, status=b.status, outcome=b.outcome, reason=b.reason, result=b.result,
                         current_tick=b.current_tick, last_tick=last or 0, started=b.started, ended=b.ended,
                         briefing=b.briefing, audience=aud, scenario=s.config(), theatre=theatre_by_id(s.theatre).to_dict(),
                         zones=[dict(side=k, polygon=v) for k, v in services.zones_of(s).items()],
                         corridors=t.corridors if t else [], terrain=dict(meta=t.meta, stats=t.stats) if t else None,
                         catalogue=b.catalogue or services.load_catalogue()))


@doc(tags=['battles'], summary='Persisted tick frames (?from=&to=, max 600 per call); fog-filtered for players')
@api_view(['GET'])
@permission_classes([permissions.AllowAny])
@access_or_403
def battle_ticks(request, bid):
    b, aud = battle_access(request, bid)
    frm = int(request.query_params.get('from', 0))
    to = min(int(request.query_params.get('to', frm + 200)), frm + 600)
    step = max(1, int(request.query_params.get('step', 1)))
    rows = Tick.objects.filter(battle=b, tick__gte=frm, tick__lte=to).order_by('tick')
    frames = []
    for r in rows:
        if (r.tick - frm) % step:
            continue
        frames.append(filter_frame(json.loads(zlib.decompress(bytes(r.frame))), aud, b.scenario.fog_enabled))
    return Response(dict(frames=frames, audience=aud))


@doc(tags=['battles'], summary='Kill feed: attacker/victim force & entity, weapon, round, tick (AR-2)')
@api_view(['GET'])
@permission_classes([permissions.AllowAny])
@access_or_403
def battle_kills(request, bid):
    b, aud = battle_access(request, bid)
    qs = Casualty.objects.filter(battle=b)
    if aud in ('BLUE', 'RED'):
        qs = qs.filter(victim_side=aud) | qs.filter(attacker_side=aud)
    return Response(list(qs.order_by('tick', 'id').values()))


@doc(tags=['battles'], summary='Entities (initial roster) of a battle')
@api_view(['GET'])
@permission_classes([permissions.AllowAny])
@access_or_403
def battle_entities(request, bid):
    b, aud = battle_access(request, bid)
    qs = Entity.objects.filter(battle=b)
    if aud in ('BLUE', 'RED'):
        qs = qs.filter(side=aud)
    return Response(list(qs.values()))


@doc(tags=['battles'], summary='Persisted events (?type=KILL|INTRUSION|HVT_ACQUIRED|INTERCEPT|ALERT|END)')
@api_view(['GET'])
@permission_classes([permissions.AllowAny])
@access_or_403
def battle_events(request, bid):
    b, aud = battle_access(request, bid)
    if aud != 'ADMIN':
        return err('available after the battle', 403)
    qs = BattleEvent.objects.filter(battle=b)
    if request.query_params.get('type'):
        qs = qs.filter(type=request.query_params['type'])
    return Response(list(qs.order_by('tick').values('tick', 'type', 'data')[:5000]))


@doc(tags=['analytics'], summary='Results dashboard data: KPIs and chart series (AR-3)')
@api_view(['GET'])
@permission_classes([permissions.AllowAny])
@access_or_403
def battle_analytics(request, bid):
    b, aud = battle_access(request, bid)
    if aud != 'ADMIN':
        return err('available after the battle', 403)
    n = Tick.objects.filter(battle=b).count()
    stride = max(1, n // 400)
    series = list(Tick.objects.filter(battle=b).order_by('tick').values(
        'tick', 'sim_time', 'blue_alive', 'red_alive', 'blue_strength', 'red_strength', 'blue_hvt_hp', 'red_hvt_hp'))[::stride]
    ents = list(Entity.objects.filter(battle=b).values('side', 'cls', 'is_hvt', 'platform_id'))
    cas = list(Casualty.objects.filter(battle=b).values())
    deployed = {s: sum(1 for e in ents if e['side'] == s and not e['is_hvt']) for s in ('BLUE', 'RED')}
    lost = {s: sum(1 for c in cas if c['victim_side'] == s and c['victim_cls'] != 'HVT') for s in ('BLUE', 'RED')}
    kills = {s: sum(1 for c in cas if c['attacker_side'] == s and c['victim_side'] != s) for s in ('BLUE', 'RED')}
    classes = sorted({e['cls'] for e in ents if not e['is_hvt']})
    attrition = {s: {c: sum(1 for x in cas if x['victim_side'] == s and x['victim_cls'] == c) for c in classes} for s in ('BLUE', 'RED')}
    fielded = {s: {c: sum(1 for e in ents if e['side'] == s and e['cls'] == c) for c in classes} for s in ('BLUE', 'RED')}
    by_weapon: dict = {}
    for c in cas:
        k = f"{c['attacker_side'] or '—'}|{c['weapon_name'] or 'unknown'}"
        by_weapon[k] = by_weapon.get(k, 0) + 1
    kill_ratio = {s: round(kills[s] / max(1, lost[s]), 2) for s in ('BLUE', 'RED')}
    last = series[-1] if series else {}
    first = series[0] if series else {}
    shots = {}
    if n:
        fr = json.loads(zlib.decompress(bytes(Tick.objects.filter(battle=b).order_by('-tick').first().frame)))
        for s_ in ('BLUE', 'RED'):
            shots[s_] = sum(1 for u in fr['units'] if u['s'] == s_)
    comparative = {}
    for s_ in ('BLUE', 'RED'):
        comparative[s_] = dict(
            firepower=round(first.get(f'{s_.lower()}_strength', 0), 1),
            survivability=round(100 * (deployed[s_] - lost[s_]) / max(1, deployed[s_]), 1),
            lethality=kills[s_],
            hvt_integrity=last.get(f'{s_.lower()}_hvt_hp', 0),
            kill_ratio=kill_ratio[s_],
        )
    return Response(dict(outcome=b.outcome, reason=b.reason, result=b.result, deployed=deployed, lost=lost,
                         surviving={s: deployed[s] - lost[s] for s in deployed}, kills=kills, kill_ratio=kill_ratio,
                         series=series, attrition=attrition, fielded=fielded, classes=classes, by_weapon=by_weapon,
                         comparative=comparative, hvts=list(HVT.objects.filter(battle=b).values()),
                         advisor=list(AdvisorLog.objects.filter(battle=b).order_by('-id').values('tick', 'side', 'kind', 'content')[:20])))


@doc(tags=['analytics'], summary='Ask the Battle — natural-language Q&A over the persisted record (AI-3)')
@api_view(['POST'])
@permission_classes([permissions.AllowAny])
@access_or_403
def battle_ask(request, bid):
    b, aud = battle_access(request, bid)
    if aud != 'ADMIN':
        return err('available after the battle', 403)
    q = str(request.data.get('question', '')).strip()[:500]
    if not q:
        return err('ask a question')
    data = dict(result=b.result, casualties=list(Casualty.objects.filter(battle=b).order_by('tick', 'id').values()),
                entities=list(Entity.objects.filter(battle=b).values()), hvts=list(HVT.objects.filter(battle=b).values()))
    ans = advisor.ask_battle(q, data)
    AdvisorLog.objects.create(battle=b, side='ALL', tick=b.current_tick, kind='ask', content=dict(q=q, **ans))
    return Response(ans)


# ── assets (PW-1, j) ───────────────────────────────────────────────────────

ASSET_MODELS = {'platforms': Platform, 'weapons': Weapon}
REQUIRED = {
    'platforms': ['name', 'cls', 'armour', 'dims', 'mass_t', 'power_kw', 'road_kph', 'offroad_kph', 'max_grade_deg', 'fuel_l',
                  'fuel_l_per_km', 'sensor', 'weapons', 'hp'],
    'weapons': ['name', 'type', 'round', 'range_m', 'dispersion_mrad', 'muzzle_vel', 'pen_mm', 'damage', 'ammo', 'reload_s'],
}


def _validate_asset(kind: str, data: dict):
    missing = [k for k in REQUIRED[kind] if k not in data]
    if missing:
        raise ValueError('missing fields: ' + ', '.join(missing))
    if kind == 'platforms':
        if data['cls'] not in PLATFORM_CLASSES:
            raise ValueError('unknown class')
        for f in ('front', 'side', 'rear', 'top'):
            float(data['armour'][f])
        weps = set(Weapon.objects.values_list('id', flat=True)) or set(default_catalogue()['weapons'])
        bad = [w for w in data['weapons'] if w not in weps]
        if bad:
            raise ValueError('unknown weapons: ' + ', '.join(bad))
    elif data['type'] not in WEAPON_TYPES:
        raise ValueError('unknown weapon type')


@doc(tags=['assets'], summary='List / create platforms or weapons (kind = platforms | weapons)')
@api_view(['GET', 'POST'])
@permission_classes([IsStaff])
def assets(request, kind):
    M = ASSET_MODELS.get(kind)
    if M is None:
        return err('kind must be platforms or weapons', 404)
    if request.method == 'GET':
        if not M.objects.exists():
            services.seed_assets()
        return Response([dict(o.data, id=o.id, updated=o.updated) for o in M.objects.order_by('id')])
    data = dict(request.data)
    aid = str(data.pop('id', '')).strip()
    if not aid:
        return err('id is required')
    base = (default_catalogue()[kind].get(aid) or next(iter(default_catalogue()[kind].values())))
    full = {**base, **data, 'id': aid}
    try:
        _validate_asset(kind, full)
        M.objects.create(id=aid, data=full)
    except IntegrityError:
        return err('an asset with this id already exists', 409)
    except (ValueError, TypeError, KeyError) as e:
        return err(str(e))
    return Response(full, status=201)


@doc(tags=['assets'], summary='Get / replace / delete one asset (applies at next battle start/reset)')
@api_view(['GET', 'PUT', 'DELETE'])
@permission_classes([IsStaff])
def asset_detail(request, kind, aid):
    M = ASSET_MODELS.get(kind)
    if M is None:
        return err('kind must be platforms or weapons', 404)
    o = get_object_or_404(M, id=aid)
    if request.method == 'GET':
        return Response(dict(o.data, id=o.id))
    if request.method == 'DELETE':
        o.delete()
        return Response(status=204)
    full = {**o.data, **request.data, 'id': aid}
    try:
        _validate_asset(kind, full)
    except (ValueError, TypeError, KeyError) as e:
        return err(str(e))
    o.data = full
    o.save()
    return Response(full)


@doc(tags=['assets'], summary='Reset the catalogue to the defaults')
@api_view(['POST'])
@permission_classes([IsStaff])
def assets_reset(request):
    services.seed_assets(reset=True)
    return Response({'ok': True})


# ── force hierarchy (o, OB-1) ──────────────────────────────────────────────

def node_json(n: OrbatNode, counts: dict) -> dict:
    return dict(id=n.id, parent_id=n.parent_id, level=n.level, name=n.name, typology=n.typology, meta=n.meta,
                children=counts.get(n.id, 0))


@doc(tags=['orbat'], summary='List nodes (?level=FORCE&parent=<id>) / create a node')
@api_view(['GET', 'POST'])
@permission_classes([IsStaff])
def orbat(request):
    if request.method == 'GET':
        qs = OrbatNode.objects.all()
        if request.query_params.get('level'):
            qs = qs.filter(level=request.query_params['level'].upper())
        if 'parent' in request.query_params:
            p = request.query_params['parent']
            qs = qs.filter(parent_id=p or None)
        counts = dict(OrbatNode.objects.values('parent_id').annotate(n=Count('id')).values_list('parent_id', 'n'))
        totals = dict(OrbatNode.objects.values('level').annotate(n=Count('id')).values_list('level', 'n'))
        return Response(dict(nodes=[node_json(n, counts) for n in qs.order_by('level', 'name')],
                             totals={lv: totals.get(lv, 0) for lv, _ in OrbatNode.LEVELS}))
    d = request.data
    name = str(d.get('name', '')).strip()[:120]
    if not name:
        return err('name is required')
    parent = OrbatNode.objects.filter(id=d.get('parent_id')).first() if d.get('parent_id') else None
    level = parent and OrbatNode.CHILD[parent.level] or 'LAND'
    if level is None:
        return err('SubUnits cannot have children')
    if OrbatNode.objects.filter(parent=parent, name__iexact=name).exists():
        return err(f'"{name}" already exists under this parent', 409)
    n = OrbatNode.objects.create(parent=parent, level=level, name=name, typology=str(d.get('typology', ''))[:60], meta=d.get('meta') or {})
    return Response(node_json(n, {}), status=201)


@doc(tags=['orbat'], summary='Rename / edit / delete a node (deleting removes its subtree)')
@api_view(['PATCH', 'DELETE'])
@permission_classes([IsStaff])
def orbat_node(request, nid):
    n = get_object_or_404(OrbatNode, id=nid)
    if request.method == 'DELETE':
        n.delete()
        return Response(status=204)
    name = str(request.data.get('name', n.name)).strip()[:120]
    if OrbatNode.objects.filter(parent_id=n.parent_id, name__iexact=name).exclude(id=n.id).exists():
        return err(f'"{name}" already exists under this parent', 409)
    n.name = name
    n.typology = str(request.data.get('typology', n.typology))[:60]
    if 'meta' in request.data:
        n.meta = request.data['meta'] or {}
    n.save()
    return Response(node_json(n, {}))


# ── tick progression for external systems (q. Interfaces) ─────────────────

@doc(tags=['battles'], summary='Battle control: start | pause | resume | step | auto (value bool) | cadence (value ms) | end')
@api_view(['POST'])
@permission_classes([IsStaff])
def control(request, sid):
    s = own_scenario(request, sid)
    room = registry.peek(s.room_code) or registry.get(s.room_code)
    if room is None:
        return err('open the lobby first', 409)
    action = request.data.get('action')
    if action not in ('start', 'pause', 'resume', 'step', 'auto', 'cadence', 'end'):
        return err('unknown action')
    try:
        room.control(None, action, request.data.get('value'))
    except RoomError as e:
        return err(str(e), 409)
    return Response(dict(status=room.sim.status, tick=room.sim.tick, battle_id=room.row.id))


@doc(tags=['play'], summary='Quick play: prepare the scenario, open a fresh lobby and join the caller as BLUE commander')
@api_view(['POST'])
@permission_classes([IsStaff])
def quickplay(request, sid):
    s = own_scenario(request, sid)
    return _quickplay(request, s)


def _quickplay(request, s):
    if s.mode == 'SKIRMISH' and s.battles.exists():
        registry.close(s.room_code)
        services.ingest_village(s)  # a fresh village for every new match
    elif not TerrainGrid.objects.filter(scenario=s).exists():
        services.ingest_terrain(s)
    services.ensure_default_zones(s)
    keys = services.active_keys(s)
    if 'BLUE' not in keys or 'RED' not in keys:
        keys = services.generate_keys(s)
    s.status = 'LOBBY'
    s.save(update_fields=['status', 'updated'])
    room = registry.open(s, new_run=True)
    token = secrets.token_urlsafe(24)
    callsign = (request.data.get('callsign') or request.user.username)[:24]
    Player.objects.create(scenario=s, side='BLUE', callsign=callsign, token_hash=hashlib.sha256(token.encode()).hexdigest())
    return Response(dict(room_code=s.room_code, token=token, keys=keys, battle_id=room.row.id,
                         play_url=f'/play/{s.room_code}/?token={token}'))


@doc(tags=['scenarios'], summary='Create a Village Skirmish (new procedural village), open its room and join as BLUE')
@api_view(['POST'])
@permission_classes([IsStaff])
def skirmish(request):
    theatre = request.data.get('theatre') or 'punjab-asal-uttar'
    if theatre not in {t.id for t in THEATRES}:
        return err('unknown theatre')
    tod = request.data.get('time_of_day', 'DAY')
    if tod not in TIME_OF_DAY:
        return err('unknown time of day')
    s = services.create_skirmish(request.user, theatre, time_of_day=tod, weather=request.data.get('weather', 'CLEAR'))
    return _quickplay(request, s)


@doc(tags=['terrain'], summary='Binary terrain for the 3D renderer: u32 meta length · meta JSON · pad4 · float32 elevation · uint8 ground')
@api_view(['GET'])
@permission_classes([permissions.AllowAny])
def terrain_binary(request, sid):
    s = get_object_or_404(Scenario, id=sid)
    b = services.load_terrain(s)
    if b is None:
        return err('terrain not ingested', 404)
    g = b.grid
    meta = json.dumps(dict(g.meta, corridors=b.corridors)).encode()
    pad = (4 - (4 + len(meta)) % 4) % 4
    import struct
    body = struct.pack('<I', len(meta)) + meta + b'\0' * pad + g.elev.astype('float32').tobytes() + g.ground.astype('uint8').tobytes()
    resp = HttpResponse(body, content_type='application/octet-stream')
    resp['Cache-Control'] = 'private, max-age=3600'
    return resp
