"""Run a complete AI-vs-AI battle through the live server (rooms, runner, persistence) — fast.

python tools/ai_battle.py [theatre] [--minutes 6] [--tick 0.5] [--cadence 15]
Prints the battle id; open /aar/<id>/ for the replay & dashboard.
"""
import sys
import time

import requests

B = 'http://127.0.0.1:8000'
arg = lambda k, d: type(d)(sys.argv[sys.argv.index(k) + 1]) if k in sys.argv else d
theatre = next((a for a in sys.argv[1:] if not a.startswith('--') and not a.replace('.', '').isdigit()), 'thar-longewala')
s = requests.Session()
s.get(B + '/login/')
H = lambda: {'X-CSRFToken': s.cookies['csrftoken'], 'Referer': B}
s.post(B + '/api/auth/login/', json={'username': 'admin', 'password': 'admin123'}, headers=H()).raise_for_status()
sc = next(x for x in s.get(B + '/api/scenarios/').json() if x['theatre'] == theatre)
sid = sc['id']
s.post(B + f'/api/scenarios/{sid}/close/', json={}, headers=H())
r = s.patch(B + f'/api/scenarios/{sid}/', json={'allow_ai_blue': True, 'tick_s': arg('--tick', 0.5), 'duration_min': arg('--minutes', 6),
                                               'cadence_ms': arg('--cadence', 20)}, headers=H())
if not r.ok:
    sys.exit(f'patch failed: {r.text}')
if not r.json().get('terrain'):
    s.post(B + f'/api/scenarios/{sid}/terrain/ingest/', json={'mode': 'real'}, headers=H())
s.post(B + f'/api/scenarios/{sid}/keys/', json={'red_mode': 'AI'}, headers=H())
bid = s.post(B + f'/api/scenarios/{sid}/open/', json={}, headers=H()).json()['battle_id']
print('battle', bid, s.post(B + f'/api/scenarios/{sid}/control/', json={'action': 'start'}, headers=H()).json())
while True:
    time.sleep(3)
    b = s.get(B + f'/api/battles/{bid}/').json()
    print(f"tick {b['current_tick']} {b['status']}", flush=True)
    if b['status'] == 'ENDED':
        print(b['reason'])
        print(f'AAR: {B}/aar/{bid}/')
        break
