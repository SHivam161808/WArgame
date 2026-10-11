"""AI Tactical Advisor (AI-1..4, AI-6, AI-7).

Every function first builds a deterministic, data-grounded analysis (the built-in analyst).
When a self-hosted LLM is configured it only rewrites the *wording*; executable orders and
facts always come from the analysis, so recommendations stay traceable and nothing the model
invents can be executed.
"""
from __future__ import annotations

import json
import math
import re

from . import llm
from .ai_agent import cluster
from .geo import bearing, grid_ref, point_in_polygon, polygon_area, polygon_centroid
from .inventory import CLASS_LABEL, FORMATION_ROLE, ROLE_IDS

COMPASS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']


def compass(deg: float) -> str:
    return COMPASS[int(((deg % 360) + 22.5) // 45) % 8]


def _enemy(side):
    return 'RED' if side == 'BLUE' else 'BLUE'


def _llm_polish(kind: str, facts: dict, draft: str) -> str | None:
    if not llm.available():
        return None
    system = ('You are a concise military tactical advisor for an armoured wargame. Rewrite the draft using ONLY the '
              'facts given. Do not invent units, numbers or positions. Keep it under 90 words. Plain text.')
    return llm.complete(system, f'{kind}\nFACTS:\n{json.dumps(facts)}\nDRAFT:\n{draft}', max_tokens=220)


# ─── Live threat assessment + ranked COAs ─────────────────────────────────────

def threat_assessment(b, side: str) -> dict:
    enemy = _enemy(side)
    pic = [b.units[i] for i in sorted(b.detected[side]) if i in b.units and b.units[i].alive]
    own = [u for u in b.alive_units(side) if not u.is_hvt]
    own_hvt = b.hvt(side)
    half = b.terrain.half_m
    forms = {}
    for f in b.formations.values():
        if f.side != side:
            continue
        m = [b.units[i] for i in f.unit_ids if b.units[i].alive]
        if m:
            forms[f.id] = dict(f=f, units=m, x=sum(u.x for u in m) / len(m), y=sum(u.y for u in m) / len(m),
                               pts=sum(u.spec['points'] * u.hp / u.max_hp for u in m))
    clusters = cluster([e for e in pic if not e.is_hvt], 900)
    worst = None
    for c in clusters:
        members = [b.units[i] for i in c['ids']]
        # closing speed toward each own formation / the HVT
        targets = [(fid, d['x'], d['y']) for fid, d in forms.items()]
        if own_hvt and own_hvt.alive:
            targets.append(('HVT', own_hvt.x, own_hvt.y))
        for name, tx, ty in targets:
            d = math.hypot(tx - c['x'], ty - c['y'])
            closing = 0.0
            for e in members:
                brg = bearing(e.x, e.y, tx, ty)
                closing += e.speed * math.cos(math.radians(e.heading - brg))
            closing /= len(members)
            ttc = d / closing if closing > 0.5 else math.inf
            score = c['points'] * (2.2 if name == 'HVT' else 1) / (1 + d / 3000) * (1.6 if ttc < 300 else 1)
            if worst is None or score > worst['score']:
                worst = dict(score=score, cluster=c, bears_on=name, dist=d, ttc=ttc, closing=closing)
    intruders = [e for e in pic if b.zones.get(side) and point_in_polygon(e.x, e.y, b.zones[side])]

    if worst is None:
        tempo, headline = 'LOW', 'No hostile contact in the current detection picture'
        risk = 'Sensor gap: the enemy disposition is unknown' if not b.hvt_acquired[side] else 'Enemy HVT located; no forces in contact'
        bears_on, detail = 'NONE', ('Recon has not yet located the enemy main body. ' if not pic else '') + \
            f"Own force: {len(own)} units. Enemy HVT {'located' if b.hvt_acquired[side] else 'not yet located'}."
    else:
        c = worst['cluster']
        ttc = worst['ttc']
        tempo = 'CRITICAL' if ttc < 120 else 'HIGH' if ttc < 300 else 'MODERATE' if ttc < 900 else 'LOW'
        comp = ', '.join(f'{n}× {k}' for k, n in sorted(_count(c['classes']).items(), key=lambda kv: -kv[1]))
        where = grid_ref(c['x'], c['y'], half)
        bears_on = worst['bears_on']
        target_label = 'the HVT' if bears_on == 'HVT' else f'formation {bears_on}'
        headline = f'{c["n"]} hostiles ({comp}) at grid {where} bearing on {target_label}'
        risk = f'{"Closing" if worst["closing"] > 0.5 else "Static"} enemy group {worst["dist"] / 1000:.1f} km from {target_label}'
        detail = (f'Group strength ≈ {c["points"]:.0f} pts; '
                  + (f'time to contact ≈ {ttc / 60:.1f} min. ' if ttc < math.inf else 'not currently closing. ')
                  + (f'{len(intruders)} intruder(s) inside own zone. ' if intruders else '')
                  + f'Detected hostiles: {len(pic)}.')
    coas = _coas(b, side, forms, clusters, worst, intruders, pic)
    out = dict(tick=b.tick, side=side, headline=headline, risk=risk, bears_on=bears_on, tempo=tempo, detail=detail,
               coas=coas[:3], source='analyst')
    polished = _llm_polish('THREAT ASSESSMENT', {k: out[k] for k in ('headline', 'risk', 'tempo', 'detail')}, detail)
    if polished:
        out['detail'], out['source'] = polished, 'llm'
    return out


def _count(items) -> dict:
    d: dict = {}
    for i in items:
        d[i] = d.get(i, 0) + 1
    return d


def _coas(b, side, forms, clusters, worst, intruders, pic) -> list[dict]:
    enemy = _enemy(side)
    half = b.terrain.half_m
    cands = []
    ch = forms.get('CHARLIE')
    if ch and clusters:
        arty = [u for u in ch['units'] if any(b.weapons[w]['indirect'] and u.ammo[w] > 0 for w in u.ammo)]
        if arty:
            tgt = max(clusters, key=lambda c: c['points'] / (1 + math.hypot(c['x'] - ch['x'], c['y'] - ch['y']) / 20000))
            rng_ok = any(b.weapons[w]['min_range_m'] <= math.hypot(tgt['x'] - u.x, tgt['y'] - u.y) <= b.weapons[w]['range_m']
                         for u in arty for w in u.ammo if b.weapons[w]['indirect'])
            if rng_ok:
                cands.append((3.0 + tgt['points'] / 20, 'Fire mission: CHARLIE on hostile concentration',
                              f'Redirect indirect fire onto {tgt["n"]} detected hostiles at grid {grid_ref(tgt["x"], tgt["y"], half)} '
                              f'({tgt["points"]:.0f} pts) — the densest target in range of {len(arty)} tubes.',
                              dict(type='fireMission', formation='CHARLIE', target=[round(tgt['x']), round(tgt['y'])], rounds=8)))
    if worst and worst['bears_on'] in forms:
        fd = forms[worst['bears_on']]
        ratio = fd['pts'] / max(1, worst['cluster']['points'])
        if ratio < 0.8 and worst['dist'] < 5000:
            away = bearing(worst['cluster']['x'], worst['cluster']['y'], fd['x'], fd['y'])
            bx = fd['x'] + math.sin(math.radians(away)) * 2000
            by = fd['y'] + math.cos(math.radians(away)) * 2000
            bx, by = b.agents[side].cover_point(*b.terrain.clamp_xy(bx, by))
            cands.append((4.0 + (0.8 - ratio) * 4, f'Withdraw {fd["f"].id} to covered ground',
                          f'{fd["f"].id} is outmatched {ratio:.1f}:1 by the group {worst["dist"] / 1000:.1f} km {compass(bearing(fd["x"], fd["y"], worst["cluster"]["x"], worst["cluster"]["y"]))}. '
                          f'Fall back 2 km {compass(away)} to concealment at grid {grid_ref(bx, by, half)} and let CHARLIE attrit them.',
                          dict(type='route', formation=fd['f'].id, waypoints=[[round(bx), round(by)]])))
    al = forms.get('ALPHA')
    if b.hvt_acquired[side] and al:
        h = b.hvt(enemy)
        if h and h.alive:
            cands.append((3.5 + al['pts'] / 30, 'Commit ALPHA on the enemy HVT',
                          f'Enemy HVT is located at grid {grid_ref(h.x, h.y, half)} with {h.hp / h.max_hp * 100:.0f}% integrity. '
                          f'ALPHA ({len(al["units"])} vehicles) can close {math.hypot(h.x - al["x"], h.y - al["y"]) / 1000:.1f} km and decide the battle.',
                          dict(type='route', formation='ALPHA', waypoints=[[round(h.x), round(h.y)]])))
    de = forms.get('DELTA')
    if not b.hvt_acquired[side] and de:
        # push recon along the avenue with the fewest detected hostiles (sensor gap)
        best, best_n = None, 1e9
        for c in b.corridors:
            path = c['path'] if side == 'BLUE' else list(reversed(c['path']))
            n = sum(1 for e in pic if min(math.hypot(e.x - p[0], e.y - p[1]) for p in path[::4]) < 2500)
            if n < best_n:
                best, best_n = path, n
        if best:
            wp = best[int(len(best) * 0.7)]
            cands.append((3.2, 'Accelerate DELTA along the quiet avenue',
                          f'The enemy HVT is still unlocated. Avenue with {best_n} detected hostiles offers a sensor gap; '
                          f'push DELTA to grid {grid_ref(wp[0], wp[1], half)} to extend sensor coverage.',
                          dict(type='route', formation='DELTA', waypoints=[[round(wp[0]), round(wp[1])]])))
    if intruders and forms.get('BRAVO'):
        cx = sum(e.x for e in intruders) / len(intruders)
        cy = sum(e.y for e in intruders) / len(intruders)
        cands.append((5.0, 'BRAVO intercept intruders in own zone',
                      f'{len(intruders)} hostile(s) are inside the own deployment zone at grid {grid_ref(cx, cy, half)}; '
                      f'BRAVO should intercept before they reach the HVT.',
                      dict(type='route', formation='BRAVO', waypoints=[[round(cx), round(cy)]])))
    if not cands:
        cands.append((1.0, 'Hold and consolidate', 'No decisive opportunity in the current picture; hold positions, '
                      'keep sensors forward and conserve ammunition.', None))
    cands.sort(key=lambda c: -c[0])
    return [dict(id=f'coa-{b.tick}-{i + 1}', rank=i + 1, title=t, rationale=r, order=o) for i, (_, t, r, o) in enumerate(cands)]


# ─── Mission briefing (AI-6) ──────────────────────────────────────────────────

def briefing(theatre, cfg: dict, zones: dict, terrain_stats: dict | None, corridors: list, plans: dict, platforms: dict) -> str:
    def power(plan):
        if not plan:
            return 0, 0
        n = pts = 0
        for f in plan.get('formations', []):
            for pid, k in f.get('composition', {}).items():
                n += k
                pts += platforms.get(pid, {}).get('points', 3) * k
        return n, pts

    bn, bp = power(plans.get('BLUE'))
    rn, rp = power(plans.get('RED'))
    ratio = bp / rp if rp else None
    lines = [f'MISSION BRIEFING — {theatre.name} ({theatre.season}), {cfg.get("time_of_day", "DAY").title()}, '
             f'weather {cfg.get("weather", "CLEAR").title()}.']
    lines.append(f'Situation: {theatre.description}')
    if terrain_stats:
        g = terrain_stats.get('ground', {})
        main = ', '.join(f'{k.lower()} {v:.0f}%' for k, v in sorted(g.items(), key=lambda kv: -kv[1])[:3])
        lines.append(f'Terrain: elevation {terrain_stats["min_elev"]}–{terrain_stats["max_elev"]} m, mean slope '
                     f'{terrain_stats["mean_slope"]}°, {terrain_stats["pct_steep"]}% steep ground, {terrain_stats["pct_water"]}% open water; '
                     f'going: {main}.')
    if zones.get('BLUE') and zones.get('RED'):
        a, c = polygon_centroid(zones['BLUE']), polygon_centroid(zones['RED'])
        sep = math.hypot(a[0] - c[0], a[1] - c[1]) / 1000
        lines.append(f'Zones: BLUE {polygon_area(zones["BLUE"]) / 1e6:.0f} km², RED {polygon_area(zones["RED"]) / 1e6:.0f} km², '
                     f'{sep:.1f} km apart; RED lies {compass(bearing(a[0], a[1], c[0], c[1]))} of BLUE.')
    if corridors:
        lens = [sum(math.hypot(q[0] - p[0], q[1] - p[1]) for p, q in zip(c['path'][:-1], c['path'][1:])) / 1000 for c in corridors]
        k = min(range(len(lens)), key=lambda i: lens[i])
        lines.append(f'Likely axis of contact: avenue {k + 1} ({lens[k]:.1f} km, the most direct of {len(corridors)} mobility corridors).')
    if bn or rn:
        bal = f'{ratio:.2f}:1' if ratio else 'n/a'
        lines.append(f'Force balance: BLUE {bn} platforms ({bp} pts) vs RED {rn} platforms ({rp} pts) — ratio {bal}.')
    lines.append('Objective: locate and destroy the enemy HVT while keeping your own intact. Recon (DELTA) must find the HVT; '
                 'fire support (CHARLIE) should strike detected concentrations.')
    text = '\n'.join(lines)
    polished = _llm_polish('MISSION BRIEFING', {'draft_lines': lines}, text)
    return polished or text


# ─── Formation suggestion (AI-4, PL-7) ────────────────────────────────────────

ROLE_PREF = {
    'ALPHA': ['MBT', 'HOVER', 'IFV', 'GUNSHIP'],
    'BRAVO': ['AD', 'IFV', 'APC', 'MBT'],
    'CHARLIE': ['SPG', 'MLRS', 'JET', 'APC'],
    'DELTA': ['RECON', 'UAV', 'HOVER'],
}


def suggest_formations(side: str, inventory: dict | None, zones: dict, terrain, corridors: list, platforms: dict) -> dict:
    """Composition, shape and doctrine per formation from inventory, zones and terrain."""
    from .inventory import default_inventory

    inv = dict(inventory or default_inventory(side))
    stats = terrain.stats() if terrain is not None else {'mean_slope': 2, 'pct_water': 0, 'pct_steep': 0}
    steep = stats['pct_steep'] > 40
    wet = stats['pct_water'] > 10
    by_cls: dict = {}
    for pid, n in inv.items():
        p = platforms.get(pid)
        if p and p['cls'] != 'HVT' and n > 0:
            by_cls.setdefault(p['cls'], []).append([pid, n])
    from .inventory import default_order_of_battle
    # same force size as the default order of battle, so suggestions stay balanced against the AI
    targets = {f['id']: sum(f['composition'].values()) for f in default_order_of_battle(side)}
    comps = {k: {} for k in targets}
    for fid in ['DELTA', 'CHARLIE', 'BRAVO', 'ALPHA']:
        pref = list(ROLE_PREF[fid])
        if wet and fid == 'ALPHA':
            pref = ['HOVER', 'IFV', 'MBT', 'GUNSHIP']  # amphibious first in marsh
        if steep and fid == 'ALPHA':
            pref = ['GUNSHIP', 'HOVER', 'MBT', 'IFV']  # air and light armour in the mountains
        for cls in pref:
            for entry in by_cls.get(cls, []):
                while entry[1] > 0 and sum(comps[fid].values()) < targets[fid]:
                    comps[fid][entry[0]] = comps[fid].get(entry[0], 0) + 1
                    entry[1] -= 1
    zone = zones.get(side) or []
    enemy_zone = zones.get(_enemy(side)) or []
    zc = polygon_centroid(zone) if zone else (0, 0)
    ec = polygon_centroid(enemy_zone) if enemy_zone else (0, 0)
    face = bearing(zc[0], zc[1], ec[0], ec[1])
    fx, fy = math.sin(math.radians(face)), math.cos(math.radians(face))
    lx, ly = math.cos(math.radians(face)), -math.sin(math.radians(face))

    def at(fwd, lat):
        p = (zc[0] + fx * fwd + lx * lat, zc[1] + fy * fwd + ly * lat)
        return [round(p[0]), round(p[1])] if not zone or point_in_polygon(p[0], p[1], zone) else [round(zc[0]), round(zc[1])]

    shapes = {'ALPHA': 'COLUMN' if steep else 'WEDGE', 'BRAVO': 'RING', 'CHARLIE': 'LINE', 'DELTA': 'LINE'}
    doctrines = {'ALPHA': 'AGGRESSIVE', 'BRAVO': 'DEFENSIVE', 'CHARLIE': 'SUPPRESSIVE', 'DELTA': 'RECON'}
    anchors = {'ALPHA': at(1500, 0), 'BRAVO': at(-1800, 0), 'CHARLIE': at(-2500, 1500), 'DELTA': at(2500, -1500)}
    formations = [dict(id=fid, name=f'{fid} ({FORMATION_ROLE[fid]})', doctrine=doctrines[fid], shape=shapes[fid],
                       composition=comps[fid], anchor=anchors[fid], facing=round(face)) for fid in targets]
    hvt = at(-2600, 0)
    why = [f'Facing {compass(face)} toward the RED zone.' if side == 'BLUE' else f'Facing {compass(face)} toward the BLUE zone.']
    if steep:
        why.append(f'{stats["pct_steep"]}% steep ground: ALPHA moves in COLUMN along the valley corridors and leads with gunships/hover armour.')
    if wet:
        why.append(f'{stats["pct_water"]}% open water: amphibious IFVs and hover tanks lead ALPHA.')
    if not steep and not wet:
        why.append('Open going: ALPHA in WEDGE for frontal firepower with flank security.')
    why.append('BRAVO rings the HVT with air defence; CHARLIE sits 2.5 km back in LINE for indirect fire; '
               'DELTA screens forward on the flank to find the enemy HVT.')
    if corridors:
        why.append(f'{len(corridors)} avenues of approach available for the advance.')
    rationale = ' '.join(why)
    polished = _llm_polish('FORMATION SUGGESTION RATIONALE', {'formations': formations, 'terrain': stats}, rationale)
    return dict(formations=formations, hvt=hvt, rationale=polished or rationale, source='llm' if polished else 'analyst')


# ─── Ask the Battle (AI-3) ────────────────────────────────────────────────────

def ask_battle(question: str, data: dict) -> dict:
    """Answer a natural-language question from the persisted record.

    data = {result, casualties: [rows], entities: [rows], events: [rows], hvts: [rows]}.
    Returns {answer, citations: [{tick, text}], source}.
    """
    q = question.strip()
    ql = q.lower()
    cas = data.get('casualties', [])
    ents = {e['entity_id']: e for e in data.get('entities', [])}
    res = data.get('result') or {}
    cites = []

    def cite(c):
        cites.append({'tick': c['tick'], 'text': f"{c['attacker_name'] or 'unknown'} → {c['victim_name']} "
                                                 f"({c['weapon_name'] or '—'}, {c['range_m'] or 0:.0f} m)"})

    def find_entity(text):
        names = sorted(ents.keys(), key=len, reverse=True)
        for n in names:
            if n.lower() in text or n.lower().replace('-', ' ') in text:
                return n
        m = re.search(r'\b(blue|red)\s+([a-z]+)[\s-]*(\d+)', text)
        if m:
            side = 'B' if m.group(1) == 'blue' else 'R'
            for n in names:
                parts = n.split('-')
                if parts[0] == side and parts[-1] == m.group(3) and m.group(2).upper().startswith(parts[1][:3]):
                    return n
        return None

    answer = None
    form = next((f for f in ('ALPHA', 'BRAVO', 'CHARLIE', 'DELTA') if f.lower() in ql), None)
    side_q = 'BLUE' if 'blue' in ql else 'RED' if 'red' in ql else None
    ent = find_entity(ql)

    if ent and re.search(r'destroy|kill|who|which', ql):
        rows = [c for c in cas if c['victim_id'] == ent]
        if rows:
            c = rows[0]
            cite(c)
            answer = (f"{ent} was destroyed at tick {c['tick']} (t+{c['sim_time']:.0f} s) by {c['attacker_name'] or 'an unknown source'}"
                      f"{' (' + CLASS_LABEL.get(c['attacker_cls'] or '', c['attacker_cls'] or '') + ', ' + (c['attacker_formation'] or '—') + ')' if c['attacker_name'] else ''}"
                      f" using {c['weapon_name'] or 'unknown weapon'} ({c['round'] or '—'}) at {c['range_m'] or 0:.0f} m.")
        else:
            kills = [c for c in cas if c['attacker_id'] == ent]
            for c in kills[:6]:
                cite(c)
            answer = (f'{ent} was not destroyed. ' + (f'It destroyed {len(kills)} enemy platform(s): ' +
                      ', '.join(c['victim_name'] for c in kills) + '.' if kills else 'It scored no kills.'))
    elif form and re.search(r'lose|lost|loss|casualt|why', ql):
        sideguess = side_q or 'BLUE'
        rows = [c for c in cas if c['victim_formation'] == form and c['victim_side'] == sideguess]
        if not rows:
            answer = f'Formation {form} ({sideguess}) suffered no losses.'
        else:
            by_w = _count([c['weapon_name'] or 'unknown' for c in rows])
            by_a = _count([c['attacker_formation'] or c['attacker_cls'] or 'unknown' for c in rows])
            by_cls = _count([c['victim_cls'] for c in rows])
            for c in rows[:8]:
                cite(c)
            avg_r = sum((c['range_m'] or 0) for c in rows) / len(rows)
            span = (rows[0]['tick'], rows[-1]['tick'])
            top_w = max(by_w, key=by_w.get)
            answer = (f'{sideguess} {form} lost {len(rows)} platform(s) (' + ', '.join(f'{n}× {k}' for k, n in by_cls.items()) +
                      f') between tick {span[0]} and {span[1]}. The main killer was {top_w} ({by_w[top_w]} of {len(rows)}); '
                      f'attackers by formation: ' + ', '.join(f'{k} {n}' for k, n in by_a.items()) +
                      f'. Average engagement range {avg_r:.0f} m' +
                      (' — losses at long range point to being out-ranged by missiles/railguns rather than a close fight.' if avg_r > 3000 else
                       ' — a close-range fight, typical of an ambush or a frontal assault.'))
    elif re.search(r'hvt', ql):
        rows = [c for c in cas if c['victim_cls'] == 'HVT']
        for c in rows:
            cite(c)
        hv = data.get('hvts', [])
        acq = '; '.join(f"{h['side']} HVT acquired by the enemy at tick {h['acquired_tick']}" for h in hv if h.get('acquired_tick') is not None)
        if rows:
            answer = ' '.join(f"The {c['victim_side']} HVT was destroyed at tick {c['tick']} by {c['attacker_name']} with {c['weapon_name']}."
                              for c in rows) + (f' ({acq}.)' if acq else '')
        else:
            hp = res.get('hvt_hp', {})
            answer = f"No HVT was destroyed. Final integrity: BLUE {hp.get('BLUE', '?')}%, RED {hp.get('RED', '?')}%." + (f' {acq}.' if acq else '')
    elif re.search(r'most kills|top|best|ace|highest', ql):
        tally = _count([c['attacker_name'] for c in cas if c['attacker_name'] and (not side_q or c['attacker_side'] == side_q)])
        top = sorted(tally.items(), key=lambda kv: -kv[1])[:5]
        for c in [c for c in cas if c['attacker_name'] == (top[0][0] if top else None)][:5]:
            cite(c)
        answer = ('Top scorers: ' + ', '.join(f'{n} ({k} kills)' for n, k in top) + '.') if top else 'No kills were recorded.'
    elif re.search(r'weapon|round|effective', ql):
        tally = _count([c['weapon_name'] or 'unknown' for c in cas if not side_q or c['attacker_side'] == side_q])
        top = sorted(tally.items(), key=lambda kv: -kv[1])
        answer = 'Kills by weapon: ' + ', '.join(f'{w} {n}' for w, n in top) + '.' if top else 'No kills were recorded.'
    elif re.search(r'how many|count|loss|lost|kills', ql):
        k = res.get('kills', {})
        lost = res.get('lost', {})
        answer = (f"Kills — BLUE {k.get('BLUE', 0)}, RED {k.get('RED', 0)}. Losses — BLUE {lost.get('BLUE', 0)} of "
                  f"{res.get('deployed', {}).get('BLUE', 0)}, RED {lost.get('RED', 0)} of {res.get('deployed', {}).get('RED', 0)}.")
    if answer is None:
        k = res.get('kills', {})
        answer = (f"{res.get('reason', 'Battle summary unavailable.')} Kills BLUE {k.get('BLUE', 0)} / RED {k.get('RED', 0)} over "
                  f"{res.get('ticks', 0)} ticks. Ask about a unit (e.g. 'Which entity destroyed R-MBT-2?'), a formation "
                  f"('Why did ALPHA lose tanks?'), the HVTs, top scorers or weapons.")
        for c in cas[:3]:
            cite(c)
    out = dict(answer=answer, citations=cites[:12], source='analyst')
    if llm.available():
        polished = llm.complete(
            'You answer questions about a finished wargame using ONLY the records provided. Cite ticks like [tick 123]. '
            'If the records do not answer the question, say so.',
            f'QUESTION: {q}\nANALYST ANSWER: {answer}\nRECORDS: {json.dumps(cites[:12])}\nRESULT: {json.dumps(res)}',
            max_tokens=300)
        if polished:
            out['answer'], out['source'] = polished, 'llm'
    return out
