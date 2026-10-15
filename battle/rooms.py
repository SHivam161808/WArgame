"""Live rooms: 4 + 4 player slots with AI fill-in, deployment, readiness and the tick runner.

One Room per scenario room code. A BattleRunner thread advances the pure-Python engine, persists
every tick and broadcasts a fog-of-war filtered frame to each audience group (BLUE / RED / ADMIN)
through the Channels layer. All mutations of engine state happen under ``room.lock``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import threading
import time
import zlib
from collections import deque

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import close_old_connections, connection, transaction
from django.utils import timezone

from sim import advisor
from sim.engine import Battle as SimBattle, filter_frame
from sim.geo import point_in_polygon
from sim.inventory import FORMATION_IDS, FORMATION_ROLE
from sim.skirmish import Skirmish
from sim.theatres import theatre_by_id

from . import loop as loopref
from . import services
from .models import AdvisorLog, Battle, BattleEvent, Casualty, Entity, HVT, Player, Scenario, Tick

log = logging.getLogger('battle')
SIDES = ('BLUE', 'RED')
PERSIST_EVENTS = {'KILL', 'HVT_ACQUIRED', 'INTRUSION', 'INTERCEPT', 'ALERT', 'END'}


class RoomError(Exception):
    pass


def group_name(code: str, audience: str) -> str:
    return f'room_{code}_{audience}'


class Room:
    def __init__(self, scenario: Scenario, new_run: bool = False):
        self.code = scenario.room_code
        self.scenario_id = scenario.id
        self.lock = threading.RLock()
        self.layer = get_channel_layer()
        self.players: dict[str, dict] = {}  # pid -> {side, slot, callsign, sockets, joined}
        self.ready = {'BLUE': False, 'RED': False}
        self.plans: dict = {'BLUE': None, 'RED': None}
        self.auto = True
        self.step_req = 0
        self.new_entities = []  # soldiers spawned mid-battle; recorded by the runner thread
        self.parked = {}  # pid -> (x, y, hp, sim_time) of a disconnected player's soldier
        self.paused = False
        self.frames: deque = deque(maxlen=900)
        self.tick_buf: list = []
        self.pending_coas: dict = {'BLUE': {}, 'RED': {}}
        self.last_advice: dict = {}
        self.thread: threading.Thread | None = None
        self.stop_evt = threading.Event()
        self._load(scenario, new_run)

    # ── setup ───────────────────────────────────────────────────────────────
    def _load(self, scenario: Scenario, new_run: bool, carry_plans: dict | None = None):
        self.parked, self.new_entities = {}, []
        self.scenario = scenario
        self.cfg = scenario.config()
        self.cadence_ms = scenario.cadence_ms
        self.fog = scenario.fog_enabled
        bundle = services.load_terrain(scenario)
        if bundle is None:
            services.ingest_terrain(scenario)
            bundle = services.load_terrain(scenario)
        self.bundle = bundle
        self.zones = services.zones_of(scenario)
        self.catalogue = services.load_catalogue()
        last = scenario.battles.order_by('-run_no').first()
        if last and last.status in ('DEPLOYMENT',) and not new_run:
            row = last
        else:
            if last and last.status == 'RUNNING':
                last.status, last.reason = 'ENDED', 'Aborted (server restart or new run)'
                last.save(update_fields=['status', 'reason'])
            row = Battle.objects.create(scenario=scenario, run_no=(last.run_no + 1) if last else 1,
                                        seed=random.randrange(1, 2 ** 31), status='DEPLOYMENT')
        self.row = row
        self.skirmish = scenario.mode == 'SKIRMISH'
        Sim = Skirmish if self.skirmish else SimBattle
        self.sim = Sim(row.id, self.cfg, bundle.grid, bundle.corridors, self.zones, self.catalogue, row.seed, nav=bundle.nav)
        self.frames.clear()
        self.tick_buf = []
        self.ready = {'BLUE': False, 'RED': False}
        self.snapshot = None
        if self.skirmish:
            self.plans = {s: dict(side=s, formations=[], hvt=None) for s in SIDES}
        elif carry_plans:
            self.plans = {s: carry_plans.get(s) for s in SIDES}
            self.snapshot = carry_plans.get('units')
        else:
            inv_b = services.inventory_for(scenario, 'BLUE')
            sug = advisor.suggest_formations('BLUE', inv_b, self.zones, bundle.grid, bundle.corridors, self.catalogue['platforms'])
            self.plans['BLUE'] = dict(side='BLUE', formations=sug['formations'], hvt=sug['hvt'])
            self.plans['RED'] = self.sim.auto_plan('RED', services.inventory_for(scenario, 'RED'), weighted=True)
        if scenario.red_mode == 'AI':
            self.ready['RED'] = True
        theatre = theatre_by_id(scenario.theatre)
        if self.skirmish:
            lay = bundle.grid.meta['village']
            self.briefing = (f"VILLAGE SKIRMISH — {theatre.name}. {len(lay['buildings'])} buildings, a school, a lake and "
                             f"{len(lay['vehicles'])} vehicles on a 600 m map. Squads of {self.sim.squad}: empty places are taken by bots and the RED squad hunts you. "
                             "Everyone starts with a pistol — loot rifles, SMGs, shotguns, sniper rifles, RPGs, medkits, "
                             "vests and helmets from the houses. No respawns: the last squad standing wins.")
        else:
            self.briefing = advisor.briefing(theatre, self.cfg, self.zones, bundle.stats, bundle.corridors,
                                             {s: self.plans[s] for s in SIDES}, self.catalogue['platforms'])
        row.briefing = self.briefing
        row.save(update_fields=['briefing'])
        Scenario.objects.filter(id=scenario.id).update(briefing=self.briefing)

    # ── players & slots (PL-4, PL-5) ────────────────────────────────────────
    def side_players(self, side: str) -> list:
        return sorted([(pid, p) for pid, p in self.players.items() if p['side'] == side], key=lambda kv: kv[1]['joined'])

    def commander(self, side: str) -> str | None:
        for pid, p in self.side_players(side):
            if p['sockets']:
                return pid
        return None

    def slot_owner(self, side: str, slot: str) -> str | None:
        for pid, p in self.players.items():
            if p['side'] == side and p['slot'] == slot:
                return pid
        return None

    def attach(self, player: Player, channel: str):
        with self.lock:
            p = self.players.get(player.id)
            if p is None:
                if player.side == 'RED' and self.scenario.red_mode == 'AI':
                    raise RoomError('RED is played by the AI Red player in this scenario')
                cap = self.sim.squad if self.skirmish else 4
                if len(self.side_players(player.side)) >= cap:
                    raise RoomError(f'This force already has {cap} players')
                slot = player.slot if player.slot and not self.slot_owner(player.side, player.slot) else None
                if slot is None:
                    slot = next((f for f in FORMATION_IDS if not self.slot_owner(player.side, f)), None)
                p = dict(side=player.side, slot=slot, callsign=player.callsign, sockets=set(), joined=time.time())
                self.players[player.id] = p
                Player.objects.filter(id=player.id).update(slot=slot or '')
            p['sockets'].add(channel)
            if self.sim.status in ('RUNNING', 'PAUSED'):
                self._spawn_soldier(player.id)
            self._sync_controllers()
        self.broadcast_lobby()

    def detach(self, pid: str, channel: str):
        with self.lock:
            p = self.players.get(pid)
            if not p:
                return
            p['sockets'].discard(channel)
            if not p['sockets']:
                a = self.sim.remove_avatar(pid)  # the soldier leaves the field (vehicle back to the AI)
                if a is not None and a.alive:
                    self.parked[pid] = (a.x, a.y, a.hp, self.sim.sim_time)
                self._sync_controllers()
        self.broadcast_lobby()

    def claim_slot(self, pid: str, slot: str):
        with self.lock:
            p = self.players[pid]
            if slot not in FORMATION_IDS:
                raise RoomError('unknown formation')
            owner = self.slot_owner(p['side'], slot)
            if owner and owner != pid:
                raise RoomError(f'{slot} is already commanded by {self.players[owner]["callsign"]}')
            p['slot'] = slot
            Player.objects.filter(id=pid).update(slot=slot)
            self._sync_controllers()
        self.broadcast_lobby()

    def _sync_controllers(self):
        """Formations whose slot has a connected human are player-controlled; all others are AI (PL-5)."""
        for (side, fid), f in self.sim.formations.items():
            owner = self.slot_owner(side, fid)
            connected = owner and self.players[owner]['sockets']
            f.controller = owner if connected else 'AI'
            if not connected:
                for uid in f.unit_ids:
                    u = self.sim.units[uid]
                    if u.controller not in ('AI', '') and u.controller not in self.players:
                        u.controller = 'AI'
        # release vehicles possessed by disconnected players
        for u in self.sim.units.values():
            if u.avatar:  # a player's own soldier follows the player's connection
                online = u.player in self.players and self.players[u.player]['sockets']
                u.controller = u.player if online else 'AI'
                continue
            if u.controller not in ('AI', '', 'BOT', 'PARKED') and not (u.controller in self.players and self.players[u.controller]['sockets']):
                u.controller = 'AI'
                u.task = 'IDLE'

    def can_command(self, pid: str, formation: str | None) -> bool:
        p = self.players.get(pid)
        if not p:
            return False
        if self.commander(p['side']) == pid:
            return True  # the commander may re-task any formation of the side
        return formation is not None and p['slot'] == formation

    # ── deployment (BP-1, BP-2) ─────────────────────────────────────────────
    def set_plan(self, pid: str | None, side: str, plan: dict):
        with self.lock:
            if self.sim.status != 'DEPLOYMENT':
                raise RoomError('deployment is closed')
            if pid is not None:
                p = self.players[pid]
                if p['side'] != side:
                    raise RoomError('not your force')
            current = self.plans[side] or dict(side=side, formations=[], hvt=None)
            new = self._validate_plan(side, plan)
            if pid is not None and self.commander(side) != pid:
                slot = self.players[pid]['slot']
                keep = [f for f in current['formations'] if f['id'] != slot]
                mine = [f for f in new['formations'] if f['id'] == slot]
                new = dict(side=side, formations=keep + mine, hvt=current.get('hvt'))
                self._validate_plan(side, new)
            self.plans[side] = new
            self.ready[side] = False if side == 'BLUE' or self.scenario.red_mode != 'AI' else True
        self.broadcast_lobby()

    def _validate_plan(self, side: str, plan: dict) -> dict:
        zone = self.zones.get(side) or []
        inv = services.inventory_for(self.scenario, side)
        totals: dict = {}
        out = []
        seen = set()
        for f in plan.get('formations', []):
            fid = f.get('id')
            if fid not in FORMATION_IDS or fid in seen:
                raise RoomError(f'bad formation id {fid}')
            seen.add(fid)
            comp = {}
            for pid_, n in (f.get('composition') or {}).items():
                n = int(n)
                spec = self.catalogue['platforms'].get(pid_)
                if spec is None or spec['cls'] == 'HVT' or n < 0:
                    raise RoomError(f'unknown platform {pid_}')
                if n:
                    comp[pid_] = n
                    totals[pid_] = totals.get(pid_, 0) + n
            anchor = f.get('anchor')
            if anchor is None or (zone and not point_in_polygon(float(anchor[0]), float(anchor[1]), zone)):
                raise RoomError(f'{fid}: deployment point must be inside the {side} zone')
            out.append(dict(id=fid, name=str(f.get('name') or f'{fid} ({FORMATION_ROLE[fid]})')[:40],
                            doctrine=f.get('doctrine', 'AGGRESSIVE'), shape=f.get('shape', 'WEDGE'),
                            composition=comp, anchor=[float(anchor[0]), float(anchor[1])],
                            facing=f.get('facing'), spacing=float(f.get('spacing', 140))))
        if inv is not None:
            for pid_, n in totals.items():
                if n > inv.get(pid_, 0):
                    raise RoomError(f'{self.catalogue["platforms"][pid_]["name"]}: {n} requested, {inv.get(pid_, 0)} allowed')
        hvt = plan.get('hvt')
        if hvt is not None and zone and not point_in_polygon(float(hvt[0]), float(hvt[1]), zone):
            raise RoomError('the HVT must be placed inside the own zone')
        return dict(side=side, formations=out, hvt=[float(hvt[0]), float(hvt[1])] if hvt else None,
                    weighted_doctrines=plan.get('weighted_doctrines', side == 'RED' and self.scenario.red_mode != 'HUMAN'))

    def set_ready(self, pid: str | None, side: str, ready: bool):
        with self.lock:
            if pid is not None and self.commander(side) != pid:
                raise RoomError('only the force commander can mark Ready')
            if ready and not self.skirmish and not (self.plans[side] and self.plans[side].get('hvt')):
                raise RoomError('place the HVT before marking Ready')
            if ready and not self.skirmish and not any(f['composition'] for f in self.plans[side]['formations']):
                raise RoomError('deploy at least one formation')
            self.ready[side] = ready
        self.broadcast_lobby()
        self.maybe_start()

    def maybe_start(self, force: bool = False):
        with self.lock:
            if self.sim.status != 'DEPLOYMENT':
                return
            blue_humans = any(p['side'] == 'BLUE' for p in self.players.values())
            if self.scenario.red_mode != 'AI' and not any(p['side'] == 'RED' and p['sockets'] for p in self.players.values()):
                self.ready['RED'] = True  # MIXED/HUMAN with nobody connected → AI takes the whole RED side
            if not force and not (self.ready['BLUE'] and self.ready['RED']):
                return
            if not force and not blue_humans and not self.scenario.allow_ai_blue:
                return
            self._start()

    def _start(self):
        s = self.sim
        s.deploy('BLUE', self.plans['BLUE'])
        s.deploy('RED', self.plans['RED'])
        for pid in sorted(self.players, key=lambda k: self.players[k]['joined']):
            if self.players[pid]['sockets']:  # players away at the start join in on reconnect
                self._spawn_soldier(pid, record=False)
        if self.snapshot:  # Play Again: identical opening positions and doctrines
            for snap in self.snapshot:
                u = s.units.get(snap['id'])
                if u:
                    u.x, u.y, u.heading, u.doctrine, u.avenue, u.detached = snap['x'], snap['y'], snap['h'], snap['d'], snap['a'], snap['det']
                    u.turret_yaw = u.heading
                    s._place(u)
        self._sync_controllers()
        s.start()
        units_snap = [dict(id=u.id, x=u.x, y=u.y, h=u.heading, d=u.doctrine, a=u.avenue, det=u.detached) for u in s.units.values()]
        with transaction.atomic():
            Entity.objects.filter(battle=self.row).delete()
            Entity.objects.bulk_create([Entity(battle=self.row, entity_id=u.id, side=u.side, formation=u.formation or '',
                                               platform_id=u.platform_id, cls=u.cls, name=u.name, init_x=u.x, init_y=u.y,
                                               init_heading=u.heading, doctrine=u.doctrine, is_hvt=u.is_hvt,
                                               controller=u.controller) for u in s.units.values()])
            HVT.objects.filter(battle=self.row).delete()
            for side in SIDES:
                h = s.hvt(side)
                if h:
                    HVT.objects.create(battle=self.row, side=side, entity_id=h.id, x=h.x, y=h.y)
            self.row.plans = dict(BLUE=self.plans['BLUE'], RED=self.plans['RED'], units=units_snap)
            self.row.catalogue = self.catalogue
            self.row.status = 'RUNNING'
            self.row.started = timezone.now()
            self.row.save()
            Scenario.objects.filter(id=self.scenario_id).update(status='RUNNING')
        self.stop_evt.clear()
        self.thread = threading.Thread(target=self._run, name=f'battle-{self.code}', daemon=True)
        self.thread.start()
        log.info('battle %s started (room %s)', self.row.id, self.code)
        self.broadcast_lobby()

    # ── battle control (BP-3, l. Battle control) ────────────────────────────
    def control(self, pid: str | None, action: str, value=None):
        with self.lock:
            if pid is not None:
                side = self.players[pid]['side']
                if self.commander(side) != pid:
                    raise RoomError('only a force commander or the controller can change the battle pace')
            if action == 'start':
                if self.sim.status == 'DEPLOYMENT':
                    if pid is None:
                        self.maybe_start(force=True)
                    else:
                        self.set_ready(pid, self.players[pid]['side'], True)
                    return
            elif action == 'pause' and self.sim.status == 'RUNNING':
                self.sim.status = 'PAUSED'
            elif action == 'resume' and self.sim.status == 'PAUSED':
                self.sim.status = 'RUNNING'
            elif action == 'step':
                if self.sim.status == 'PAUSED':
                    self.sim.status = 'RUNNING'
                    self.auto = False
                self.step_req += 1
            elif action == 'auto':
                self.auto = bool(value)
                if self.sim.status == 'PAUSED' and self.auto:
                    self.sim.status = 'RUNNING'
            elif action == 'cadence':
                self.cadence_ms = int(max(20, min(5000, int(value))))
            elif action == 'end' and pid is None:
                self.sim.finish('Ended by the Exercise Controller')
        self.broadcast_lobby()

    def set_fog(self, enabled: bool):
        with self.lock:
            self.fog = enabled
            Scenario.objects.filter(id=self.scenario_id).update(fog_enabled=enabled)
        self.broadcast_lobby()

    # ── orders (BP-5, LS-5) ─────────────────────────────────────────────────
    ALLOWED = {'route', 'doctrine', 'shape', 'halt', 'fireMission', 'rejoin', 'enter', 'exit', 'summon', 'reload', 'heal', 'pick'}

    def command(self, pid: str, cmd: dict):
        with self.lock:
            p = self.players[pid]
            side = p['side']
            typ = cmd.get('type')
            if typ not in self.ALLOWED:
                raise RoomError('unknown order')
            fid = cmd.get('formation')
            units = cmd.get('unit_ids') or []
            if fid and (side, fid) not in self.sim.formations:
                raise RoomError('unknown formation')
            if fid and not self.can_command(pid, fid):
                raise RoomError(f'{fid} is commanded by another player')
            for uid in units:
                u = self.sim.units.get(uid)
                if u is None or u.side != side:
                    raise RoomError('not your unit')
                if not self.can_command(pid, u.formation):
                    raise RoomError(f'{uid} belongs to another player\'s formation')
            if self.sim.status == 'DEPLOYMENT':
                raise RoomError('battle has not started')
            self.sim.submit(side, pid, cmd)

    def possess(self, pid: str, unit_id: str | None):
        with self.lock:
            p = self.players[pid]
            u = self.sim.units.get(unit_id) if unit_id else None
            if u is not None and u.avatar:
                if u.player != pid:
                    raise RoomError('that soldier is another player')
            elif unit_id:
                if u is None or u.side != p['side'] or not u.alive or u.is_hvt:
                    raise RoomError('cannot take control of that unit')
                if not self.can_command(pid, u.formation):
                    raise RoomError('that vehicle belongs to another player\'s formation')
                if u.controller not in ('AI', '', pid):
                    raise RoomError('another player is driving that vehicle')
            self.sim.submit(p['side'], pid, {'type': 'possess', 'unit_id': unit_id})

    def drive(self, pid: str, inp: dict):
        u_id = inp.get('unit_id')
        u = self.sim.units.get(u_id) if u_id else None
        if u is None or u.controller != pid:
            return
        clean = {k: inp[k] for k in ('throttle', 'steer', 'strafe', 'sprint', 'crouch', 'turret_yaw', 'gun_pitch', 'fire',
                                     'weapon', 'aim', 'climb', 'ads') if k in inp}
        # latch trigger pulls: drive messages arrive ~20/s but ticks may be slower — never lose a click
        prev = self.sim.drive.get(u.id) or {}
        clean['fire_latch'] = bool(prev.get('fire_latch')) or bool(clean.get('fire'))
        if clean.get('aim') is not None:
            a = clean['aim']
            clean['aim'] = [float(a[0]), float(a[1]), float(a[2])]
        self.sim.set_drive(u.id, clean)

    # ── player soldiers & team chat ─────────────────────────────────────────
    def _spawn_soldier(self, pid: str, record: bool = True):
        p = self.players[pid]
        park = self.parked.pop(pid, None)
        near = park[:2] if park and self.sim.sim_time - park[3] < 120 else None  # quick reconnect: back where you were
        u = self.sim.spawn_avatar(p['side'], pid, p['callsign'], near)
        if u is not None and near:
            u.hp = park[2]
        if u is not None and record:
            self.new_entities.append(Entity(battle=self.row, entity_id=u.id, side=u.side, formation='',
                                            platform_id=u.platform_id, cls=u.cls, name=u.name, init_x=u.x, init_y=u.y,
                                            init_heading=u.heading, doctrine=u.doctrine, is_hvt=False, controller=pid))
        return u

    def chat(self, pid: str, text: str, to_all: bool = False):
        p = self.players[pid]
        text = ' '.join(str(text).split())[:200]
        if not text:
            return
        msg = {'type': 'chat', 'from': p['callsign'], 'side': p['side'], 'all': bool(to_all), 'text': text,
               't': round(self.sim.sim_time, 1)}
        for aud in (('BLUE', 'RED', 'ADMIN') if to_all else (p['side'], 'ADMIN')):
            self.send(aud, msg)

    # ── advisor (AI-1, AI-2) ────────────────────────────────────────────────
    def advise(self, side: str) -> dict:
        if self.skirmish:
            raise RoomError('the tactical advisor is for armoured battles')
        with self.lock:
            a = advisor.threat_assessment(self.sim, side)
        self.pending_coas[side] = {c['id']: c['order'] for c in a['coas']}
        self.last_advice[side] = a
        AdvisorLog.objects.create(battle_id=self.row.id, side=side, tick=a['tick'], kind='threat', content=a)
        return a

    def apply_coa(self, pid: str, coa_id: str):
        side = self.players[pid]['side']
        order = self.pending_coas[side].get(coa_id)
        if order is None:
            raise RoomError('recommendation expired — request a fresh assessment')
        self.command(pid, order)
        AdvisorLog.objects.create(battle_id=self.row.id, side=side, tick=self.sim.tick, kind='coa_applied',
                                  content=dict(coa=coa_id, order=order, by=self.players[pid]['callsign']))

    def suggest(self, side: str) -> dict:
        s = advisor.suggest_formations(side, services.inventory_for(self.scenario, side), self.zones, self.bundle.grid,
                                       self.bundle.corridors, self.catalogue['platforms'])
        AdvisorLog.objects.create(battle_id=self.row.id, side=side, tick=0, kind='suggestion', content=s)
        return s

    # ── runner (LS-1, SC-3) ─────────────────────────────────────────────────
    def _run(self):
        close_old_connections()
        try:
            while not self.stop_evt.is_set():
                t0 = time.perf_counter()
                stepped = False
                with self.lock:
                    s = self.sim
                    if s.status == 'RUNNING' and (self.auto or self.step_req > 0):
                        fr = s.step()
                        casualties = list(s.casualties)
                        if not self.auto:
                            self.step_req = max(0, self.step_req - 1)
                            if self.step_req == 0:
                                s.status = 'PAUSED'
                                fr['status'] = 'PAUSED'
                        stepped = True
                    elif s.status == 'ENDED':
                        break
                if stepped:
                    self._on_frame(fr, casualties)
                    if fr['status'] == 'ENDED':
                        break
                    delay = self.cadence_ms / 1000 - (time.perf_counter() - t0)
                    if delay > 0:
                        self.stop_evt.wait(delay)
                else:
                    self.stop_evt.wait(0.05)
            self._flush()
            if self.sim.status == 'ENDED':
                self._on_end()
        except Exception:
            log.exception('battle runner crashed (room %s)', self.code)
        finally:
            connection.close()  # this thread's DB connection

    def _on_frame(self, fr: dict, casualties: list):
        self.frames.append(fr)
        s = self.sim

        def strength(side):
            return round(sum(u.spec['points'] * u.hp / u.max_hp for u in s.units.values() if u.side == side and u.alive and not u.is_hvt), 1)

        hb, hr = s.hvt('BLUE'), s.hvt('RED')
        self.tick_buf.append(Tick(
            battle_id=self.row.id, tick=fr['tick'], sim_time=fr['sim_time'], ts=fr['ts'],
            frame=zlib.compress(json.dumps(fr, separators=(',', ':')).encode(), 3),
            blue_alive=sum(1 for u in s.units.values() if u.side == 'BLUE' and u.alive and not u.is_hvt),
            red_alive=sum(1 for u in s.units.values() if u.side == 'RED' and u.alive and not u.is_hvt),
            blue_strength=strength('BLUE'), red_strength=strength('RED'),
            blue_hvt_hp=round(hb.hp / hb.max_hp * 100, 1) if hb else 0, red_hvt_hp=round(hr.hp / hr.max_hp * 100, 1) if hr else 0))
        if casualties:
            Casualty.objects.bulk_create([Casualty(battle_id=self.row.id, **c) for c in casualties])
        evs = [e for e in fr['events'] if e['t'] in PERSIST_EVENTS]
        if evs:
            BattleEvent.objects.bulk_create([BattleEvent(battle_id=self.row.id, tick=fr['tick'], type=e['t'], data=e) for e in evs])
            for e in evs:
                if e['t'] == 'HVT_ACQUIRED':
                    HVT.objects.filter(battle_id=self.row.id, entity_id=e['hvt'], acquired_tick__isnull=True).update(acquired_tick=fr['tick'])
                elif e['t'] == 'KILL' and e['victim'].endswith('-HVT'):
                    HVT.objects.filter(battle_id=self.row.id, entity_id=e['victim']).update(destroyed_tick=fr['tick'])
        if len(self.tick_buf) >= 20:
            self._flush()
        self.broadcast_frame(fr)
        every = max(20, int(self.cfg.get('advisor_every', 240)))
        if fr['tick'] % every == 0 and not self.skirmish:
            for side in SIDES:
                if any(p['side'] == side and p['sockets'] for p in self.players.values()):
                    try:
                        a = self.advise(side)
                        self.send(side, {'type': 'advisor', 'assessment': a})
                    except Exception:
                        log.exception('advisor failed')

    def _flush(self):
        if self.new_entities:
            ents, self.new_entities = self.new_entities, []
            known = set(Entity.objects.filter(battle=self.row).values_list('entity_id', flat=True))
            Entity.objects.bulk_create([e for e in ents if e.entity_id not in known])
        if not self.tick_buf:
            return
        buf, self.tick_buf = self.tick_buf, []
        Tick.objects.bulk_create(buf, ignore_conflicts=True)
        Battle.objects.filter(id=self.row.id).update(current_tick=buf[-1].tick)

    def _on_end(self):
        res = self.sim.result()
        now = timezone.now()
        Battle.objects.filter(id=self.row.id).update(status='ENDED', result=res, outcome=res['outcome'] or '',
                                                      reason=res['reason'], ended=now, current_tick=self.sim.tick)
        Scenario.objects.filter(id=self.scenario_id).update(status='ENDED')
        self.row.refresh_from_db()
        log.info('battle %s ended: %s', self.row.id, res['reason'])
        for aud in ('BLUE', 'RED', 'ADMIN'):
            self.send(aud, {'type': 'ended', 'result': res, 'battle_id': self.row.id})
        self.broadcast_lobby()

    def play_again(self, pid: str | None):
        with self.lock:
            if self.sim.status != 'ENDED':
                raise RoomError('the battle is still in progress')
            if pid is not None and self.commander(self.players[pid]['side']) != pid:
                raise RoomError('only a force commander can restart')
            carry = dict(self.row.plans or {})
            scenario = Scenario.objects.get(id=self.scenario_id)
            Scenario.objects.filter(id=self.scenario_id).update(status='LOBBY')
            scenario.status = 'LOBBY'
            self._load(scenario, new_run=True, carry_plans=carry)
            self._sync_controllers()
        for aud in ('BLUE', 'RED', 'ADMIN'):
            self.send(aud, {'type': 'rerun', 'battle_id': self.row.id})
        self.broadcast_lobby()
        return self.row.id

    def shutdown(self):
        self.stop_evt.set()

    # ── views & broadcast ───────────────────────────────────────────────────
    def history(self, audience: str, frm: int, to: int) -> list:
        frm, to = max(0, int(frm)), int(to)
        to = min(to, frm + 400)
        have = {f['tick']: f for f in self.frames if frm <= f['tick'] <= to}
        missing = [t for t in range(frm, to + 1) if t not in have]
        if missing:
            self._flush()
            for row in Tick.objects.filter(battle_id=self.row.id, tick__gte=min(missing), tick__lte=max(missing)):
                have[row.tick] = json.loads(zlib.decompress(bytes(row.frame)))
        return [filter_frame(have[t], audience, self.fog) for t in sorted(have)]

    def lobby_state(self, audience: str) -> dict:
        with self.lock:
            slots = []
            for side in SIDES:
                cmd = self.commander(side)
                for fid in FORMATION_IDS:
                    owner = self.slot_owner(side, fid)
                    p = self.players.get(owner) if owner else None
                    slots.append(dict(side=side, slot=fid, role=FORMATION_ROLE[fid], player_id=owner,
                                      callsign=p['callsign'] if p else None, connected=bool(p and p['sockets']),
                                      commander=owner is not None and owner == cmd))
            plans = {s: self.plans[s] for s in SIDES if audience in (s, 'ADMIN')}
            return dict(battle_id=self.row.id, status=self.sim.status, run_no=self.row.run_no, slots=slots,
                        ready=dict(self.ready), plans=plans, briefing=self.briefing, red_mode=self.scenario.red_mode,
                        fog_enabled=self.fog, auto=self.auto, cadence_ms=self.cadence_ms, tick=self.sim.tick,
                        max_ticks=self.sim.max_ticks, tick_s=self.sim.dt, sim_time=self.sim.sim_time,
                        outcome=self.sim.outcome, reason=self.sim.reason,
                        commanders={s: self.commander(s) for s in SIDES},
                        players=[dict(id=pid, side=p['side'], slot=p['slot'], callsign=p['callsign'], connected=bool(p['sockets']))
                                 for pid, p in self.players.items() if audience in (p['side'], 'ADMIN')])

    def session_info(self, audience: str) -> dict:
        b = self.bundle
        side = audience if audience in SIDES else None
        inv = services.inventory_for(self.scenario, side) if side else {s: services.inventory_for(self.scenario, s) for s in SIDES}
        zones = [dict(side=s, polygon=p) for s, p in self.zones.items() if audience == 'ADMIN' or s == side or not self.fog]
        return dict(role='ADMIN' if audience == 'ADMIN' else 'PLAYER', side=side, scenario=self.cfg, battle_id=self.row.id,
                    room_code=self.code, zones=zones, corridors=b.corridors,
                    terrain=dict(meta={k: v for k, v in b.grid.meta.items() if k != 'village'}, stats=b.stats), inventory=inv,
                    theatre=theatre_by_id(self.scenario.theatre).to_dict(),
                    catalogue=self.catalogue, briefing=self.briefing)

    def send(self, audience: str, msg: dict):
        payload = {'type': 'relay', 'text': json.dumps(msg, separators=(',', ':'))}
        group = group_name(self.code, audience)
        try:
            loop = loopref.main_loop
            if loop is not None and loop.is_running():
                # thread-safe hand-off to the server loop that owns the channel layer
                asyncio.run_coroutine_threadsafe(self.layer.group_send(group, payload), loop)
            else:
                async_to_sync(self.layer.group_send)(group, payload)
        except Exception:
            log.exception('broadcast failed')

    def join_frame(self, audience: str) -> dict:
        """Latest frame for a newly connected socket (with the current loot list in a skirmish)."""
        fr = filter_frame(self.frames[-1], audience, self.fog if audience != 'ADMIN' else True)
        if self.skirmish and 'items' not in fr and getattr(self.sim, 'items', None) is not None:
            fr = dict(fr, items=[[i['id'], i['k'], i['x'], i['y'], i['z']] for i in list(self.sim.items.values())])
        return fr

    def broadcast_frame(self, fr: dict):
        for aud in ('BLUE', 'RED', 'ADMIN'):
            self.send(aud, {'type': 'frame', 'frame': filter_frame(fr, aud, self.fog)})

    def broadcast_lobby(self):
        for aud in ('BLUE', 'RED', 'ADMIN'):
            self.send(aud, {'type': 'lobby', 'lobby': self.lobby_state(aud)})


class Registry:
    def __init__(self):
        self.rooms: dict[str, Room] = {}
        self.lock = threading.Lock()

    def peek(self, code: str) -> Room | None:
        return self.rooms.get(code)

    def get(self, code: str) -> Room | None:
        with self.lock:
            room = self.rooms.get(code)
            if room is not None:
                return room
            sc = Scenario.objects.filter(room_code=code).first()
            if sc is None or sc.status == 'SETUP':
                return None
            room = Room(sc)
            self.rooms[code] = room
            return room

    def open(self, scenario: Scenario, new_run: bool = False) -> Room:
        with self.lock:
            old = self.rooms.pop(scenario.room_code, None)
            if old:
                old.shutdown()
            room = Room(scenario, new_run=new_run)
            self.rooms[scenario.room_code] = room
            return room

    def close(self, code: str):
        with self.lock:
            room = self.rooms.pop(code, None)
        if room:
            room.shutdown()


registry = Registry()
