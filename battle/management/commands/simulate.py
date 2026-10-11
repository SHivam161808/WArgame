"""python manage.py simulate --theatre thar-longewala --ticks 3600 [--tick-s 0.5] [--seed 7]

Runs a complete AI-vs-AI battle headless on real terrain and prints the result —
useful for balance testing and CI smoke tests.
"""
import time
from collections import Counter

from django.conf import settings
from django.core.management.base import BaseCommand

from sim.engine import Battle
from sim.inventory import catalogue
from sim.pathfind import CORRIDOR_WIDTH_M, generate_corridors
from sim.terrarium import flat_terrain, ingest_terrarium
from sim.theatres import THEATRE_BY_ID, default_zones, theatre_by_id


class Command(BaseCommand):
    help = 'Run a headless AI vs AI battle'

    def add_arguments(self, parser):
        parser.add_argument('--theatre', default='thar-longewala', choices=list(THEATRE_BY_ID))
        parser.add_argument('--ticks', type=int, default=3600)
        parser.add_argument('--tick-s', type=float, default=0.5)
        parser.add_argument('--seed', type=int, default=7)
        parser.add_argument('--flat', action='store_true')

    def handle(self, *args, **o):
        t = theatre_by_id(o['theatre'])
        g = (flat_terrain(t.lat, t.lon, 18, 50, t) if o['flat'] else
             ingest_terrarium(t.lat, t.lon, 18, 50, t, settings.WARGAME['TILE_CACHE'], settings.WARGAME['OFFLINE_TERRAIN']))
        zones = default_zones(t, 18)
        cor = generate_corridors(g, zones)
        g.set_corridors([c['path'] for c in cor], CORRIDOR_WIDTH_M)
        b = Battle('headless', dict(theatre=t.id, tick_s=o['tick_s'], max_ticks=o['ticks'], weather=t.default_weather,
                                    time_of_day='DAY'), g, cor, zones, catalogue(), o['seed'])
        b.deploy('BLUE', b.auto_plan('BLUE'))
        b.deploy('RED', b.auto_plan('RED', weighted=True))
        b.start()
        ev = Counter()
        t0 = time.time()
        while b.status == 'RUNNING':
            for e in b.step()['events']:
                ev[e['t']] += 1
        r = b.result()
        self.stdout.write(self.style.SUCCESS(f"{r['outcome']}: {r['reason']}"))
        self.stdout.write(f"kills {r['kills']} lost {r['lost']} HVT hp {r['hvt_hp']} — {b.tick} ticks in {time.time() - t0:.1f}s")
        self.stdout.write(f'events {dict(ev)}')
