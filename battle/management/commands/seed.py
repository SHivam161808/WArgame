"""python manage.py seed [--reset-assets] [--demo]

Creates the administrator account, the default platform/weapon catalogue, a sample
order-of-battle register and (with --demo) one ready-to-open scenario per theatre.
"""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from battle import services
from battle.models import OrbatNode, Scenario
from sim.theatres import THEATRES

ORBAT = {
    'India': {
        'Armoured Corps': {'31 Armoured Division': ['4 Horse', '18 Cavalry', '62 Cavalry'],
                           '1 Armoured Division': ['Poona Horse', 'Hodson\'s Horse']},
        'Mechanised Infantry': {'Mechanised Infantry Brigade 12': ['1 Mech Inf', '7 Mech Inf']},
        'Artillery': {'Corps Artillery Brigade 41': ['Rail-Gun Regiment 271', 'Pinaka Regiment 214']},
        'Army Aviation': {'Attack Helicopter Squadron 251': ['Flight A', 'Flight B']},
        'Air Defence': {'Air Defence Brigade 3': ['Akash Regiment 15']},
    },
    'OPFOR (Exercise)': {
        'Armoured Corps': {'Red Armoured Division 6': ['Red Armoured Regiment 11', 'Red Armoured Regiment 13']},
        'Artillery': {'Red Artillery Brigade 9': ['Red SPG Regiment 22']},
    },
}


class Command(BaseCommand):
    help = 'Seed admin user, catalogue, ORBAT and demo scenarios'

    def add_arguments(self, parser):
        parser.add_argument('--reset-assets', action='store_true')
        parser.add_argument('--demo', action='store_true', help='create one demo scenario per theatre')

    def handle(self, *args, **opts):
        U = get_user_model()
        cfg = settings.WARGAME
        user, created = U.objects.get_or_create(username=cfg['ADMIN_USER'], defaults={'is_staff': True, 'is_superuser': True})
        if created:
            user.set_password(cfg['ADMIN_PASSWORD'])
            user.save()
            self.stdout.write(self.style.SUCCESS(f'admin user "{user.username}" created (password from ADMIN_PASSWORD)'))
        services.seed_assets(reset=opts['reset_assets'])
        self.stdout.write('catalogue ready')
        if not OrbatNode.objects.exists():
            for land, forces in ORBAT.items():
                l = OrbatNode.objects.create(level='LAND', name=land)
                for force, units in forces.items():
                    f = OrbatNode.objects.create(parent=l, level='FORCE', name=force, typology=force)
                    for unit, subs in units.items():
                        u = OrbatNode.objects.create(parent=f, level='UNIT', name=unit)
                        for s in subs:
                            OrbatNode.objects.create(parent=u, level='SUBUNIT', name=s)
            self.stdout.write('ORBAT register seeded')
        if opts['demo']:
            for t in THEATRES:
                if Scenario.objects.filter(owner=user, theatre=t.id).exists():
                    continue
                s = Scenario.objects.create(owner=user, name=f'Exercise {t.name.split(" — ")[-1]}', theatre=t.id,
                                            description=t.description, weather=t.default_weather,
                                            room_code=services.room_code(), origin_lat=t.lat, origin_lon=t.lon)
                services.ensure_default_zones(s)
                services.generate_keys(s)
                self.stdout.write(f'demo scenario {s.name} [{s.room_code}]')
