"""End-to-end API smoke flow against a running server.

Logs in as admin, ingests terrain for a demo scenario, generates force keys, opens the lobby
and joins as a BLUE player. Prints (and saves to data/smoke_room.json) the room + token.
Usage: python tools/smoke_flow.py [theatre-id] [--base http://127.0.0.1:8000]
"""
import json
import sys
from pathlib import Path

import requests

args = [a for a in sys.argv[1:] if not a.startswith('--')]
B = sys.argv[sys.argv.index('--base') + 1] if '--base' in sys.argv else 'http://127.0.0.1:8000'
theatre = args[0] if args else 'thar-longewala'
s = requests.Session()
s.get(B + '/login/')
H = lambda: {'X-CSRFToken': s.cookies['csrftoken'], 'Referer': B}
r = s.post(B + '/api/auth/login/', json={'username': 'admin', 'password': 'admin123'}, headers=H())
r.raise_for_status()
sc = next(x for x in s.get(B + '/api/scenarios/').json() if x['theatre'] == theatre)
s.post(B + f"/api/scenarios/{sc['id']}/close/", json={}, headers=H())
s.patch(B + f"/api/scenarios/{sc['id']}/", json={'tick_s': 0.25, 'duration_min': 45, 'cadence_ms': 250, 'allow_ai_blue': False}, headers=H())
r = s.post(B + f"/api/scenarios/{sc['id']}/terrain/ingest/", json={'mode': 'real'}, headers=H())
print('terrain:', r.json().get('note'))
keys = s.post(B + f"/api/scenarios/{sc['id']}/keys/", json={'red_mode': 'AI'}, headers=H()).json()['keys']
r = s.post(B + f"/api/scenarios/{sc['id']}/open/", json={}, headers=H())
r.raise_for_status()
j = requests.post(B + '/api/join/', json={'room_code': sc['room_code'], 'force_key': keys['BLUE'], 'callsign': 'Tiger-1'}).json()
out = {'room': sc['room_code'], 'token': j['token'], 'sid': sc['id'], 'keys': keys, 'battle_id': j['battle_id']}
Path('data/smoke_room.json').write_text(json.dumps(out))
print(json.dumps(out))
print(f"play: {B}/play/{sc['room_code']}/?token={j['token']}")
