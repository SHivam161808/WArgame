"""Headless browser check of the battle page on the real GPU (Vulkan via ANGLE).

python tools/browser_check.py fps            → FPS in lobby and battle (clicks READY)
python tools/browser_check.py split          → FPS without each rendering subsystem
python tools/browser_check.py shot OUT.png [seconds] [--ready] [--url URL]
python tools/browser_check.py soldier OUTDIR  → play as the soldier: sprint, aim, 1st person, board a vehicle (screenshots)
python tools/browser_check.py skirmish OUTDIR → new Village Skirmish: loot, village views, fight (screenshots)
Requires `pip install playwright` and a Chromium (playwright install chromium).
"""
import glob
import json
import sys
import time

from playwright.sync_api import sync_playwright

mode = sys.argv[1] if len(sys.argv) > 1 else 'fps'
if mode == 'skirmish':  # a fresh village match, joined as BLUE
    import requests
    _s = requests.Session(); _s.get('http://127.0.0.1:8000/login/')
    _h = lambda: {'X-CSRFToken': _s.cookies['csrftoken'], 'Referer': 'http://127.0.0.1:8000'}
    _s.post('http://127.0.0.1:8000/api/auth/login/', json={'username': 'admin', 'password': 'admin123'}, headers=_h()).raise_for_status()
    _r = _s.post('http://127.0.0.1:8000/api/skirmish/', json={'theatre': sys.argv[3] if len(sys.argv) > 3 else 'punjab-asal-uttar'}, headers=_h())
    _r.raise_for_status(); _j = _r.json()
    room = {'room': _j['room_code'], 'token': _j['token'], 'keys': _j['keys']}
    print('skirmish room', room['room'], 'keys', room['keys'])
else:
    room = json.load(open('data/smoke_room.json'))
url = f"http://127.0.0.1:8000/play/{room['room']}/?token={room['token']}"
if '--url' in sys.argv:
    url = sys.argv[sys.argv.index('--url') + 1]
ARGS = ['--use-angle=vulkan', '--enable-features=Vulkan,VulkanFromANGLE,DefaultANGLEVulkan', '--use-vulkan=native',
        '--ignore-gpu-blocklist', '--disable-vulkan-surface', '--enable-gpu']
FPS = """() => new Promise(r => { let n=0; const t0=performance.now(); function f(){ n++; if (performance.now()-t0 < 2500) requestAnimationFrame(f); else r(n/((performance.now()-t0)/1000)); } requestAnimationFrame(f); })"""
SPLIT = [
    ('node-writes', "S.scene.dbgNoNodes=!V"),
    ('matrix-writes', "S.scene.dbgNoMatrix=!V"),
    ('effects', "S.scene.effects.bb.show=V; S.scene.effects.lines.show=V"),
    ('labels/bars', "S.scene.labels.show=V; S.scene.bb.show=V"),
    ('models', "for (const v of S.scene.units.values()) if (v.model) v.model.show=V"),
]
exe = (glob.glob('/home/shivam/.cache/ms-playwright/chromium-*/chrome-linux*/chrome') or [None])[-1]
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=exe, args=ARGS)
    pg = b.new_page(viewport={'width': 1440, 'height': 810})
    logs = []
    pg.on('pageerror', lambda e: logs.append(f'PAGEERROR {e}'[:400]))
    pg.on('console', lambda m: logs.append(f'{m.type}: {m.text}'[:300]) if m.type in ('error', 'warning') else None)
    if '--login' in sys.argv:
        pg.goto('http://127.0.0.1:8000/login/')
        pg.fill('input[name=username]', 'admin'); pg.fill('input[name=password]', 'admin123')
        pg.click('button[type=submit], form button')
        time.sleep(1)
    pg.goto(url)
    time.sleep(10)
    if mode in ('fps', 'split', 'profile', 'soldier', 'skirmish') or '--ready' in sys.argv:
        if mode == 'fps':
            print('lobby fps', round(pg.evaluate(FPS), 1))
        if pg.is_visible('#lb-ready'):
            pg.click('#lb-ready')
        time.sleep(15)
    if mode == 'fps':
        print('battle fps', round(pg.evaluate(FPS), 1), 'units', pg.evaluate('() => window.__S.scene?.units.size'), 'tick', pg.evaluate('() => window.__S.frame?.tick'))
    elif mode == 'split':
        print('base', round(pg.evaluate(FPS), 1))
        for name, js in SPLIT:
            pg.evaluate('() => { const S = window.__S; const V=false; ' + js + ' }')
            time.sleep(0.5)
            f = pg.evaluate(FPS)
            pg.evaluate('() => { const S = window.__S; const V=true; ' + js + ' }')
            print(f'without {name}: {f:.1f}')
    elif mode == 'profile':
        import collections
        cdp = pg.context.new_cdp_session(pg)
        print('profiling...', flush=True)
        cdp.send('Profiler.enable'); cdp.send('Profiler.setSamplingInterval', {'interval': 500}); cdp.send('Profiler.start')
        time.sleep(6)
        prof = cdp.send('Profiler.stop')['profile']
        nodes = {n['id']: n for n in prof['nodes']}
        parent = {c: n['id'] for n in prof['nodes'] for c in n.get('children', [])}
        selft = collections.Counter(); incl = collections.Counter()
        for sid, d in zip(prof['samples'], prof['timeDeltas']):
            cf = nodes[sid]['callFrame']
            selft[f"{cf['functionName'] or '(anon)'} {cf['url'].split('/')[-1]}:{cf['lineNumber']}"] += d
            seen = set(); n = sid
            while n is not None:
                cf = nodes[n]['callFrame']
                k = f"{cf['functionName'] or '(anon)'} {cf['url'].split('/')[-1]}:{cf['lineNumber']}"
                if k not in seen: incl[k] += d; seen.add(k)
                n = parent.get(n)
        tot = sum(selft.values())
        print('SELF'); [print(f'{v/tot*100:5.1f}% {k}') for k, v in selft.most_common(12)]
        print('INCLUSIVE (our code)'); [print(f'{v/tot*100:5.1f}% {k}') for k, v in incl.most_common(80) if '.js:' in k and 'Cesium' not in k][:15]
    elif mode == 'soldier':
        out = sys.argv[2]
        st = lambda: pg.evaluate("""() => { const S = window.__S, d = S.drive, me = S.frame.units.find(u => u.av && u.pl === S.me);
            return { driving: d.unitId, inf: d.inf, me: me && { id: me.id, x: me.x, y: me.y, hp: me.hp, mt: me.mt, st: me.st },
                     fps: Math.round(S.ground.fps), prompt: document.querySelector('#h-prompt').textContent,
                     squad: document.querySelector('#h-squad').textContent, anim: S.ground.units.get(me?.id)?.anim }; }""")
        print('start', st())
        # a teammate joins mid-battle in a second browser and says hello on team chat
        import requests
        tok2 = requests.post('http://127.0.0.1:8000/api/join/', json={'room_code': room['room'], 'force_key': room['keys']['BLUE'], 'callsign': 'Cobra-2'}).json()['token']
        pg2 = b.new_page(viewport={'width': 960, 'height': 540})
        pg2.on('pageerror', lambda e: logs.append(f'PAGE2 {e} {e.stack}'[:900]))
        pg2.goto(f"http://127.0.0.1:8000/play/{room['room']}/?token={tok2}")
        time.sleep(14)
        pg2.keyboard.down('d'); time.sleep(1.5); pg2.keyboard.up('d')  # step out to the side
        pg2.keyboard.press('Enter'); pg2.keyboard.type('Cobra on your right, moving up'); pg2.keyboard.press('Enter')
        time.sleep(2)
        print('with teammate', st(), 'chat:', pg.inner_text('#chat-log'))
        pg.screenshot(path=f'{out}/s0_team.png')
        pg2.screenshot(path=f'{out}/s0_team2.png')
        pg.screenshot(path=f'{out}/s1_idle.png')
        pg.keyboard.down('Shift'); pg.keyboard.down('w'); time.sleep(3)
        pg.screenshot(path=f'{out}/s2_sprint.png')
        print('sprint', st())
        pg.keyboard.up('Shift'); pg.keyboard.up('w')
        pg.evaluate("() => window.__S.drive.toggleScope()"); time.sleep(1)
        pg.screenshot(path=f'{out}/s3_ads.png')
        pg.evaluate("() => { window.__S.drive.toggleScope(); window.__S.ground.infFP = true; }"); time.sleep(1)
        pg.screenshot(path=f'{out}/s4_fp.png')
        pg.evaluate("() => { window.__S.ground.infFP = false; }")
        for i in range(6):  # a few bursts at the horizon
            pg.evaluate("() => { window.__S.drive.fireOnce = true; }"); time.sleep(0.4)
        # call a ride, run towards it and take the controls
        pg.keyboard.press('t'); time.sleep(0.5)
        t0 = time.time(); boarded = False
        pg.keyboard.down('Shift'); pg.keyboard.down('w')
        while time.time() - t0 < 240:
            info = pg.evaluate("""() => { const S = window.__S, me = S.frame.units.find(u => u.av && u.pl === S.me);
                if (me.mt) return { mt: me.mt, prompt: '', d: 0, name: me.mt };
                const cat = S.session.catalogue.platforms;
                const vs = S.frame.units.filter(v => v.s === me.s && v.st !== 'DESTROYED' && !v.hvt && v.c !== 'INF' && !cat[v.p].air && v.pk === S.me);
                vs.sort((a, b) => Math.hypot(a.x - me.x, a.y - me.y) - Math.hypot(b.x - me.x, b.y - me.y));
                const v = vs[0]; if (!v) return null;
                S.ground.aim.yaw = (Math.atan2(v.x - me.x, v.y - me.y) * 180 / Math.PI + 360) % 360;
                return { d: Math.round(Math.hypot(v.x - me.x, v.y - me.y)), name: v.n, prompt: document.querySelector('#h-prompt').textContent, mt: me.mt }; }""")
            if info and info['mt']:
                boarded = True; break
            if info and 'TAKE' in info['prompt']:
                pg.keyboard.press('f')
            time.sleep(0.25)
        pg.keyboard.up('Shift'); pg.keyboard.up('w')
        print('boarded', boarded, info, 'after', round(time.time() - t0), 's')
        time.sleep(2)
        print('in vehicle', st())
        pg.screenshot(path=f'{out}/s5_vehicle.png')
        pg.keyboard.press('f'); time.sleep(2.5)
        print('got out', st())
        pg.screenshot(path=f'{out}/s6_out.png')
    elif mode == 'skirmish':
        out = sys.argv[2]
        st = lambda: pg.evaluate("""() => { const S = window.__S, d = S.drive, me = S.frame.units.find(u => u.av && u.pl === S.me);
            return { driving: d.unitId, me: me && { x: me.x, y: me.y, hp: Math.round(me.hp), wp: me.wp, mg: me.mg, st: me.st },
                     fps: Math.round(S.ground.fps), prompt: document.querySelector('#h-prompt').textContent,
                     alive: S.frame.alive, items: S.ground.loot?.items.size, units: S.frame.units.length,
                     squad: document.querySelector('#h-squad').textContent }; }""")
        print('start', st())
        pg.screenshot(path=f'{out}/k1_spawn.png')
        # walk to the starter weapon beside the spawn and pick it up
        t0 = time.time()
        while time.time() - t0 < 25:
            info = pg.evaluate("""() => { const S = window.__S, me = S.frame.units.find(u => u.av && u.pl === S.me);
                const it = [...S.ground.loot.items.values()].filter(i => i.k !== 'ammo').sort((a, b) => Math.hypot(a.x - me.x, a.y - me.y) - Math.hypot(b.x - me.x, b.y - me.y))[0];
                if (!it) return null;
                S.ground.aim.yaw = (Math.atan2(it.x - me.x, it.y - me.y) * 180 / Math.PI + 360) % 360;
                return { d: Math.hypot(it.x - me.x, it.y - me.y), k: it.k, prompt: document.querySelector('#h-prompt').textContent }; }""")
            if info and 'PICK UP' in info['prompt']:
                pg.keyboard.up('w'); pg.keyboard.press('f'); time.sleep(1.0); break
            pg.keyboard.down('w'); time.sleep(0.25)
        pg.keyboard.up('w')
        print('after pickup', st())
        pg.screenshot(path=f'{out}/k2_pickup.png')
        pg.evaluate("() => window.__S.drive.toggleScope()"); time.sleep(0.8)
        pg.screenshot(path=f'{out}/k3_ads.png')
        pg.evaluate("() => window.__S.drive.toggleScope()")
        # ride the motorbike parked at the spawn
        t0 = time.time(); rode = False
        while time.time() - t0 < 20:
            info = pg.evaluate("""() => { const S = window.__S, me = S.frame.units.find(u => u.av && u.pl === S.me);
                if (me.mt) return { mt: me.mt };
                const v = S.frame.units.filter(x => x.s === 'NEUT' && x.st !== 'DESTROYED').sort((a, b) => Math.hypot(a.x - me.x, a.y - me.y) - Math.hypot(b.x - me.x, b.y - me.y))[0];
                S.ground.aim.yaw = (Math.atan2(v.x - me.x, v.y - me.y) * 180 / Math.PI + 360) % 360;
                return { d: Math.hypot(v.x - me.x, v.y - me.y), n: v.n, prompt: document.querySelector('#h-prompt').textContent }; }""")
            if info.get('mt'):
                rode = True; break
            if 'DRIVE' in info.get('prompt', ''):
                pg.keyboard.up('w'); pg.keyboard.press('f'); time.sleep(0.6); continue
            pg.keyboard.down('w'); time.sleep(0.25)
        pg.keyboard.up('w')
        if rode:
            pg.keyboard.down('w'); time.sleep(4); pg.keyboard.up('w')
            print('riding', st(), pg.evaluate("() => { const S = window.__S; const v = S.frame.units.find(u => u.id === S.drive.unitId); return v && { id: v.id, kph: Math.round(v.v * 3.6), s: v.s }; }"))
            pg.screenshot(path=f'{out}/k3b_bike.png')
            pg.keyboard.press('f'); time.sleep(1)
        print('rode', rode)
        pg.keyboard.press('m'); time.sleep(0.6)
        pg.screenshot(path=f'{out}/k4_map.png')
        pg.keyboard.press('m')
        # aerial and street views of the village (free camera)
        pg.keyboard.press('Escape'); time.sleep(0.5)
        lay = pg.evaluate("""() => { const L = window.__S.ground.village.L; const s = L.buildings.find(b => b.k === 'school');
            return { school: [s.cx, s.cy, s.zb], n: L.buildings.length, centre: L.buildings.slice(0, 40).reduce((a, b) => [a[0] + b.cx / 40, a[1] + b.cy / 40], [0, 0]) }; }""")
        print('village', lay)
        pg.evaluate(f"() => {{ const G = window.__S.ground; G.flyTo({lay['school'][0]}, {lay['school'][1]}, 140); }}"); time.sleep(5)
        pg.screenshot(path=f'{out}/k5_school.png')
        pg.evaluate(f"() => {{ const G = window.__S.ground; G.flyTo({lay['centre'][0]}, {lay['centre'][1]}, 260); }}"); time.sleep(5)
        pg.screenshot(path=f'{out}/k6_village.png')
        pg.evaluate(f"""() => {{ const G = window.__S.ground, c = G.camera, x = {lay['school'][0]} - 40, y = {lay['school'][1]} - 35;
            G.controls.target.set({lay['school'][0]}, {lay['school'][2]} + 2, -({lay['school'][1]})); c.position.set(x, G.terrain.height(x, y) + 2.2, -y); }}"""); time.sleep(4)
        pg.screenshot(path=f'{out}/k7_street.png')
        print('end', st())
    elif mode == 'shot':
        time.sleep(float(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3][0].isdigit() else 5)
        pg.screenshot(path=sys.argv[2], timeout=120000)
    for line in logs[-15:]:
        print(line)
    b.close()
