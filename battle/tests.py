"""API / rooms / WebSocket integration tests (run: python manage.py test battle).

They use the flat-terrain fallback so no network is needed.
"""
import asyncio
import json
import time

from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase
from rest_framework.test import APIClient

from config.asgi import application

from . import services
from .models import Battle, Casualty, OrbatNode, Scenario, Tick
from .rooms import RoomError, registry


def closing(fn):
    """Run fn in a worker thread and close that thread's DB connection afterwards (test DB teardown needs it)."""
    def run(*a, **kw):
        from django.db import connection
        try:
            return fn(*a, **kw)
        finally:
            connection.close()
    return run


class Base(TransactionTestCase):
    def setUp(self):
        U = get_user_model()
        self.admin = U.objects.create_user('ctl', password='pass12345', is_staff=True)
        self.other = U.objects.create_user('ctl2', password='pass12345', is_staff=True)
        self.api = APIClient()
        self.api.force_authenticate(self.admin)
        services.seed_assets()

    def tearDown(self):
        rooms = list(registry.rooms.values())
        for code in list(registry.rooms):
            registry.close(code)
        for r in rooms:  # let runner threads exit and release their DB connections
            if r.thread:
                r.thread.join(timeout=5)

    def make_scenario(self, **kw):
        body = dict(name='Test', theatre='thar-longewala', tick_s=0.5, duration_min=2, cadence_ms=20, red_mode='AI')
        body.update(kw)
        r = self.api.post('/api/scenarios/', body, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        sid = r.json()['id']
        self.assertEqual(self.api.post(f'/api/scenarios/{sid}/terrain/ingest/', {'mode': 'flat'}, format='json').status_code, 200)
        keys = self.api.post(f'/api/scenarios/{sid}/keys/', {'red_mode': kw.get('red_mode', 'AI')}, format='json').json()
        return Scenario.objects.get(id=sid), keys['keys']


class ApiTests(Base):
    def test_scenario_crud_and_owner_scoping(self):
        sc, _ = self.make_scenario()
        self.assertEqual(len(self.api.get('/api/scenarios/').json()), 1)
        other = APIClient()
        other.force_authenticate(self.other)
        self.assertEqual(other.get('/api/scenarios/').json(), [])
        self.assertEqual(other.get(f'/api/scenarios/{sc.id}/').status_code, 404)
        r = self.api.patch(f'/api/scenarios/{sc.id}/', {'tick_s': 99}, format='json')
        self.assertEqual(r.status_code, 400)

    def test_anonymous_cannot_admin(self):
        anon = APIClient()
        self.assertIn(anon.get('/api/scenarios/').status_code, (401, 403))
        self.assertIn(anon.get('/api/assets/platforms/').status_code, (401, 403))

    def test_zone_validation_and_corridors(self):
        sc, _ = self.make_scenario()
        bad = self.api.put(f'/api/scenarios/{sc.id}/zones/BLUE/', {'polygon': [[0, 0], [1, 1]]}, format='json')
        self.assertEqual(bad.status_code, 400)
        outside = self.api.put(f'/api/scenarios/{sc.id}/zones/BLUE/', {'polygon': [[0, 0], [90000, 0], [0, 900]]}, format='json')
        self.assertEqual(outside.status_code, 400)
        ok = self.api.put(f'/api/scenarios/{sc.id}/zones/BLUE/', {'polygon': [[-9000, -9000], [-2000, -9000], [-2000, -2000], [-9000, -2000]], 'method': 'lasso'}, format='json')
        self.assertEqual(ok.status_code, 200)
        self.assertTrue(ok.json()['corridors'])

    def test_readiness_and_open(self):
        sc, _ = self.make_scenario()
        r = self.api.get(f'/api/scenarios/{sc.id}/readiness/').json()
        self.assertTrue(r['can_open'])
        self.assertEqual(self.api.post(f'/api/scenarios/{sc.id}/open/', {}, format='json').status_code, 200)

    def test_join_rules(self):
        sc, keys = self.make_scenario()
        anon = APIClient()
        r = anon.post('/api/join/', {'room_code': sc.room_code, 'force_key': keys['BLUE'], 'callsign': 'x'}, format='json')
        self.assertEqual(r.status_code, 409)  # lobby not open yet
        self.api.post(f'/api/scenarios/{sc.id}/open/', {}, format='json')
        self.assertEqual(anon.post('/api/join/', {'room_code': sc.room_code, 'force_key': 'BLU-NOPE', 'callsign': 'x'}, format='json').status_code, 403)
        self.assertEqual(anon.post('/api/join/', {'room_code': sc.room_code, 'force_key': keys['RED'], 'callsign': 'x'}, format='json').status_code, 409)  # RED is AI
        for i in range(4):
            self.assertEqual(anon.post('/api/join/', {'room_code': sc.room_code, 'force_key': keys['BLUE'], 'callsign': f'p{i}'}, format='json').status_code, 200)
            room = registry.get(sc.room_code)
            from .models import Player
            p = Player.objects.filter(scenario=sc).order_by('-joined').first()
            room.attach(p, f'chan{i}')
        self.assertEqual(anon.post('/api/join/', {'room_code': sc.room_code, 'force_key': keys['BLUE'], 'callsign': 'p5'}, format='json').status_code, 409)

    def test_orbat_duplicate_rejected(self):
        land = self.api.post('/api/orbat/', {'name': 'India'}, format='json').json()
        f = self.api.post('/api/orbat/', {'name': 'Armour', 'parent_id': land['id']}, format='json')
        self.assertEqual(f.json()['level'], 'FORCE')
        dup = self.api.post('/api/orbat/', {'name': 'armour', 'parent_id': land['id']}, format='json')
        self.assertEqual(dup.status_code, 409)
        totals = self.api.get('/api/orbat/').json()['totals']
        self.assertEqual(totals['LAND'], 1)
        self.assertEqual(OrbatNode.objects.count(), 2)

    def test_asset_validation(self):
        r = self.api.put('/api/assets/platforms/b-mbt-vajra/', {'weapons': ['no-such-gun']}, format='json')
        self.assertEqual(r.status_code, 400)
        r = self.api.put('/api/assets/platforms/b-mbt-vajra/', {'road_kph': 80}, format='json')
        self.assertEqual(r.json()['road_kph'], 80)

    def test_openapi_schema(self):
        r = self.api.get('/api/schema/')
        self.assertEqual(r.status_code, 200)


class RoomTests(Base):
    def run_async(self, coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    def test_full_battle_over_websocket_with_fog(self):
        sc, keys = self.make_scenario(duration_min=1)
        self.api.post(f'/api/scenarios/{sc.id}/open/', {}, format='json')
        tok = APIClient().post('/api/join/', {'room_code': sc.room_code, 'force_key': keys['BLUE'], 'callsign': 'Tiger'}, format='json').json()['token']

        async def flow():
            ws = WebsocketCommunicator(application, f'/ws/battle/{sc.room_code}/', headers=[(b'origin', b'http://testserver'), (b'host', b'testserver')])
            ok, _ = await ws.connect()
            self.assertTrue(ok)
            await ws.send_json_to({'type': 'join', 'token': tok, 'rid': 1})
            msgs = {}
            while 'reply' not in msgs or 'lobby' not in msgs or 'session' not in msgs:
                m = await ws.receive_json_from(timeout=10)
                msgs[m['type']] = m
            self.assertTrue(msgs['reply']['ok'])
            self.assertEqual(msgs['session']['session']['side'], 'BLUE')
            lobby = msgs['lobby']['lobby']
            self.assertNotIn('RED', lobby['plans'])  # never leak the enemy deployment
            plan = lobby['plans']['BLUE']
            # deploying outside the zone is rejected server-side
            bad = json.loads(json.dumps(plan))
            bad['formations'][0]['anchor'] = [17000, 17000]
            await ws.send_json_to({'type': 'deploy_plan', 'plan': bad, 'rid': 2})
            r = await self._reply(ws, 2)
            self.assertFalse(r['ok'])
            await ws.send_json_to({'type': 'ready', 'ready': True, 'rid': 3})
            self.assertTrue((await self._reply(ws, 3))['ok'])
            frames = 0
            ended = None
            deadline = time.time() + 60
            while time.time() < deadline and not ended:
                m = await ws.receive_json_from(timeout=20)
                if m['type'] == 'frame':
                    frames += 1
                    fr = m['frame']
                    seen = set(fr['detected'].get('BLUE', []))
                    for u in fr['units']:
                        if u['s'] == 'RED' and fr['status'] != 'ENDED':
                            self.assertTrue(u['id'] in seen or u['hvt'], f"fog leak: {u['id']}")
                            self.assertNotIn('am', u)  # no enemy ammo/fuel telemetry
                elif m['type'] == 'ended':
                    ended = m
            await ws.disconnect()
            from asgiref.sync import sync_to_async
            from django.db import connections
            await sync_to_async(connections.close_all)()  # the consumer worker thread's connection
            return frames, ended

        frames, ended = self.run_async(flow())
        self.assertGreater(frames, 50)
        self.assertIsNotNone(ended)
        b = Battle.objects.get(id=ended['battle_id'])
        self.assertEqual(b.status, 'ENDED')
        self.assertIn(b.outcome, ('BLUE', 'RED', 'DRAW', 'STALEMATE'))
        self.assertGreater(Tick.objects.filter(battle=b).count(), 100)
        # after-action endpoints for the owner
        a = self.api.get(f'/api/battles/{b.id}/analytics/').json()
        self.assertIn('series', a)
        self.assertEqual(self.api.get(f'/api/battles/{b.id}/ticks/?from=1&to=5').status_code, 200)
        ans = self.api.post(f'/api/battles/{b.id}/ask/', {'question': 'how many kills?'}, format='json').json()
        self.assertIn('Kills', ans['answer'])
        self.assertEqual(Casualty.objects.filter(battle=b).count(), sum(b.result['kills'].values()) or Casualty.objects.filter(battle=b).count())

    async def _reply(self, ws, rid):
        while True:
            m = await ws.receive_json_from(timeout=10)
            if m['type'] == 'reply' and m['rid'] == rid:
                return m


class MultiplayerTests(Base):
    def run_async(self, coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    async def connect(self, code, tok):
        ws = WebsocketCommunicator(application, f'/ws/battle/{code}/', headers=[(b'origin', b'http://testserver'), (b'host', b'testserver')])
        await ws.connect()
        await ws.send_json_to({'type': 'join', 'token': tok, 'rid': 1})
        await RoomTests._reply(None, ws, 1)
        return ws

    def test_mixed_mode_humans_on_both_sides_then_play_again(self):
        sc, keys = self.make_scenario(red_mode='MIXED', duration_min=0.5)
        self.api.post(f'/api/scenarios/{sc.id}/open/', {}, format='json')
        c = APIClient()
        j = lambda side, name: c.post('/api/join/', {'room_code': sc.room_code, 'force_key': keys[side], 'callsign': name}, format='json').json()['token']
        blue1, blue2, red1 = j('BLUE', 'Tiger'), j('BLUE', 'Cobra'), j('RED', 'Viper')

        async def flow():
            b1 = await self.connect(sc.room_code, blue1)
            b2 = await self.connect(sc.room_code, blue2)
            r1 = await self.connect(sc.room_code, red1)
            room = registry.get(sc.room_code)
            lob = room.lobby_state('ADMIN')
            mine = {s['callsign']: s for s in lob['slots'] if s['callsign']}
            self.assertTrue(mine['Tiger']['commander'] and not mine['Cobra']['commander'])
            self.assertEqual(mine['Viper']['side'], 'RED')
            self.assertNotEqual(mine['Tiger']['slot'], mine['Cobra']['slot'])
            # non-commander cannot mark the force ready
            await b2.send_json_to({'type': 'ready', 'ready': True, 'rid': 5})
            self.assertFalse((await RoomTests._reply(None, b2, 5))['ok'])
            # RED human must ready too in MIXED mode
            await b1.send_json_to({'type': 'ready', 'ready': True, 'rid': 6})
            self.assertTrue((await RoomTests._reply(None, b1, 6))['ok'])
            self.assertEqual(room.sim.status, 'DEPLOYMENT')
            await r1.send_json_to({'type': 'ready', 'ready': True, 'rid': 7})
            self.assertTrue((await RoomTests._reply(None, r1, 7))['ok'])
            await asyncio.sleep(0.5)
            self.assertIn(room.sim.status, ('RUNNING', 'ENDED'))
            # every connected player fights as a soldier
            souls = {u.player: u for u in room.sim.units.values() if u.avatar}
            self.assertEqual(set(souls), set(room.players))
            self.assertEqual({room.players[p]['side'] for p in souls}, {'BLUE', 'RED'})
            # team chat reaches squad mates, not the enemy
            await b1.send_json_to({'type': 'chat', 'text': 'contact front', 'rid': 11})
            async def wait_chat(ws, secs=5):
                t_end = asyncio.get_event_loop().time() + secs
                while asyncio.get_event_loop().time() < t_end:
                    try:
                        m = await ws.receive_json_from(timeout=max(0.1, t_end - asyncio.get_event_loop().time()))
                    except (asyncio.TimeoutError, asyncio.CancelledError):
                        return None
                    if m['type'] == 'chat':
                        return m
                return None
            got = await wait_chat(b2)
            self.assertEqual((got['from'], got['text']), ('Tiger', 'contact front'))
            sent, orig = [], room.send
            room.send = lambda aud, msg: sent.append(aud) if msg.get('type') == 'chat' else orig(aud, msg)
            tiger = next(p for p, v in room.players.items() if v['callsign'] == 'Tiger')
            room.chat(tiger, 'team only')
            room.chat(tiger, 'gg', to_all=True)
            room.send = orig
            self.assertEqual(sent, ['BLUE', 'ADMIN', 'BLUE', 'RED', 'ADMIN'])
            # formation controllers: human slots → player ids, others → AI
            ctl = {(f.side, f.id): f.controller for f in room.sim.formations.values()}
            self.assertEqual(sum(1 for v in ctl.values() if v != 'AI'), 3)
            # a player cannot order another player's formation
            other_slot = mine['Tiger']['slot']
            await b2.send_json_to({'type': 'cmd', 'cmd': {'type': 'halt', 'formation': other_slot}, 'rid': 8})
            self.assertFalse((await RoomTests._reply(None, b2, 8))['ok'])
            from asgiref.sync import sync_to_async
            # wait for the end, then Play Again from identical opening positions
            for _ in range(200):
                if room.sim.status == 'ENDED':
                    break
                await asyncio.sleep(0.2)
            self.assertEqual(room.sim.status, 'ENDED')
            first_battle = room.row.id
            from .models import Entity
            first = await sync_to_async(closing(lambda: {e.entity_id: (round(e.init_x), round(e.init_y)) for e in Entity.objects.filter(battle_id=first_battle)}), thread_sensitive=False)()
            await asyncio.sleep(0.5)
            await b1.send_json_to({'type': 'play_again', 'rid': 9})
            r = await RoomTests._reply(None, b1, 9)
            self.assertTrue(r['ok'])
            self.assertNotEqual(r['battle_id'], first_battle)
            from asgiref.sync import sync_to_async
            await sync_to_async(closing(room.maybe_start), thread_sensitive=False)(force=True)
            second = room.row.id
            again = await sync_to_async(closing(lambda: {e.entity_id: (round(e.init_x), round(e.init_y)) for e in Entity.objects.filter(battle_id=second)}), thread_sensitive=False)()
            self.assertEqual(first.keys(), again.keys())
            for k in first:  # identical opening positions
                self.assertLess(abs(first[k][0] - again[k][0]) + abs(first[k][1] - again[k][1]), 3, k)
            for ws in (b1, b2, r1):
                await ws.disconnect()
            from asgiref.sync import sync_to_async
            from django.db import connections
            await sync_to_async(connections.close_all)()

        self.run_async(flow())


class SkirmishTests(Base):
    def test_village_skirmish_room_with_bots(self):
        r = self.api.post('/api/skirmish/', {'theatre': 'punjab-asal-uttar'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        j = r.json()
        self.assertTrue(j['play_url'].startswith('/play/') and set(j['keys']) == {'BLUE', 'RED'})
        sc = Scenario.objects.get(room_code=j['room_code'])
        self.assertEqual(sc.mode, 'SKIRMISH')
        room = registry.get(j['room_code'])
        self.assertEqual(type(room.sim).__name__, 'Skirmish')
        info = room.session_info('BLUE')
        self.assertEqual(info['scenario']['mode'], 'SKIRMISH')
        self.assertNotIn('village', info['terrain']['meta'])  # the layout ships with heights.bin instead
        body = self.api.get(f'/api/scenarios/{sc.id}/terrain/heights.bin').content
        import struct
        meta = json.loads(body[4:4 + struct.unpack('<I', body[:4])[0]])
        self.assertIn('village', meta)
        self.assertGreater(len(meta['village']['buildings']), 60)
        # READY needs no formation or HVT here; bots fill both squads
        room.set_ready(None, 'BLUE', True)
        room.maybe_start(force=True)  # nobody connected in the test → start like the controller's Force start
        for _ in range(100):
            if room.sim.status == 'RUNNING' and room.sim.tick > 10:
                break
            time.sleep(0.05)
        self.assertEqual(room.sim.squad_alive(), {'BLUE': 4, 'RED': 4})
        fr = room.join_frame('BLUE')
        self.assertIn('items', fr)
        self.assertTrue(any(u['s'] == 'NEUT' for u in fr['units']))  # parked vehicles are always visible
        with self.assertRaises(RoomError):
            room.advise('BLUE')
