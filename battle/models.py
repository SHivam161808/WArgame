"""Relational data model (SA-4): every scenario, force key, terrain grid, zone, entity, HVT,
tick, casualty, asset and ORBAT node is a queryable record."""
import secrets

from django.conf import settings
from django.db import models

SIDE_CHOICES = [('BLUE', 'BLUE'), ('RED', 'RED')]


def short_id() -> str:
    return secrets.token_hex(6)


class Scenario(models.Model):
    STATUS = [('SETUP', 'Setup'), ('LOBBY', 'Lobby open'), ('RUNNING', 'Running'), ('ENDED', 'Ended')]
    RED_MODES = [('AI', 'AI Red player'), ('HUMAN', 'Human'), ('MIXED', 'Humans + AI fill')]
    MODES = [('ARMOUR', 'Armoured battle'), ('SKIRMISH', 'Village Skirmish')]

    id = models.CharField(primary_key=True, max_length=16, default=short_id, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='scenarios')
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    theatre = models.CharField(max_length=40)
    mode = models.CharField(max_length=10, choices=MODES, default='ARMOUR')
    time_of_day = models.CharField(max_length=10, default='DAY')
    weather = models.CharField(max_length=10, default='CLEAR')
    tick_s = models.FloatField(default=0.25, help_text='Simulated seconds per tick')
    cadence_ms = models.IntegerField(default=250, help_text='Real milliseconds between ticks on Auto')
    max_ticks = models.IntegerField(default=10800)
    ao_half_km = models.FloatField(default=18)
    cell_m = models.FloatField(default=50)
    room_code = models.CharField(max_length=8, unique=True)
    red_mode = models.CharField(max_length=6, choices=RED_MODES, default='AI')
    fog_enabled = models.BooleanField(default=True)
    advisor_every = models.IntegerField(default=240, help_text='Ticks between automatic threat assessments')
    origin_lat = models.FloatField()
    origin_lon = models.FloatField()
    status = models.CharField(max_length=8, choices=STATUS, default='SETUP')
    inventory = models.JSONField(default=dict, blank=True)  # {'BLUE': {pid: n} | None, 'RED': ...}
    briefing = models.TextField(blank=True)
    allow_ai_blue = models.BooleanField(default=False, help_text='Allow an AI-vs-AI demo start without BLUE humans')
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated']

    def __str__(self):
        return f'{self.name} [{self.room_code}]'

    @property
    def duration_s(self) -> float:
        return self.tick_s * self.max_ticks

    def config(self) -> dict:
        return dict(id=self.id, name=self.name, description=self.description, theatre=self.theatre,
                    time_of_day=self.time_of_day, weather=self.weather, tick_s=self.tick_s, cadence_ms=self.cadence_ms,
                    max_ticks=self.max_ticks, ao_half_km=self.ao_half_km, cell_m=self.cell_m, room_code=self.room_code,
                    red_mode=self.red_mode, mode=self.mode, fog_enabled=self.fog_enabled, advisor_every=self.advisor_every,
                    origin=dict(lat=self.origin_lat, lon=self.origin_lon), status=self.status)


class TerrainGrid(models.Model):
    scenario = models.OneToOneField(Scenario, on_delete=models.CASCADE, primary_key=True, related_name='terrain')
    meta = models.JSONField()
    stats = models.JSONField()
    corridors = models.JSONField(default=list)
    elev = models.BinaryField()  # zlib(float32 rows × cols)
    source_note = models.CharField(max_length=200, blank=True)
    created = models.DateTimeField(auto_now=True)


class Zone(models.Model):
    scenario = models.ForeignKey(Scenario, on_delete=models.CASCADE, related_name='zones')
    side = models.CharField(max_length=4, choices=SIDE_CHOICES)
    polygon = models.JSONField()  # [[x, y], ...] local metres
    method = models.CharField(max_length=10, default='vertices')  # vertices | lasso | default
    created = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['scenario', 'side'], name='one_zone_per_side')]


class ForceKey(models.Model):
    key = models.CharField(primary_key=True, max_length=40)
    scenario = models.ForeignKey(Scenario, on_delete=models.CASCADE, related_name='keys')
    side = models.CharField(max_length=4, choices=SIDE_CHOICES)
    revoked = models.BooleanField(default=False)
    created = models.DateTimeField(auto_now_add=True)


class Battle(models.Model):
    id = models.CharField(primary_key=True, max_length=16, default=short_id, editable=False)
    scenario = models.ForeignKey(Scenario, on_delete=models.CASCADE, related_name='battles')
    run_no = models.IntegerField(default=1)
    status = models.CharField(max_length=12, default='DEPLOYMENT')
    seed = models.IntegerField()
    current_tick = models.IntegerField(default=0)
    plans = models.JSONField(default=dict, blank=True)  # opening deployment (for Play Again)
    result = models.JSONField(null=True, blank=True)
    outcome = models.CharField(max_length=10, blank=True)
    reason = models.TextField(blank=True)
    catalogue = models.JSONField(default=dict, blank=True)  # platform/weapon snapshot used by this run
    briefing = models.TextField(blank=True)
    started = models.DateTimeField(null=True, blank=True)
    ended = models.DateTimeField(null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created']


class Entity(models.Model):
    battle = models.ForeignKey(Battle, on_delete=models.CASCADE, related_name='entities')
    entity_id = models.CharField(max_length=24)
    side = models.CharField(max_length=4)
    formation = models.CharField(max_length=8, blank=True)
    platform_id = models.CharField(max_length=40)
    cls = models.CharField(max_length=10)
    name = models.CharField(max_length=60)
    init_x = models.FloatField()
    init_y = models.FloatField()
    init_heading = models.FloatField()
    doctrine = models.CharField(max_length=12, blank=True)
    is_hvt = models.BooleanField(default=False)
    controller = models.CharField(max_length=40, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['battle', 'entity_id'], name='unique_entity_per_battle')]


class HVT(models.Model):
    battle = models.ForeignKey(Battle, on_delete=models.CASCADE, related_name='hvts')
    side = models.CharField(max_length=4)
    entity_id = models.CharField(max_length=24)
    x = models.FloatField()
    y = models.FloatField()
    acquired_tick = models.IntegerField(null=True, blank=True)
    destroyed_tick = models.IntegerField(null=True, blank=True)


class Tick(models.Model):
    battle = models.ForeignKey(Battle, on_delete=models.CASCADE, related_name='ticks')
    tick = models.IntegerField()
    sim_time = models.FloatField()
    ts = models.BigIntegerField()
    frame = models.BinaryField()  # zlib(JSON full unfiltered frame)
    blue_alive = models.IntegerField(default=0)
    red_alive = models.IntegerField(default=0)
    blue_strength = models.FloatField(default=0)
    red_strength = models.FloatField(default=0)
    blue_hvt_hp = models.FloatField(default=100)
    red_hvt_hp = models.FloatField(default=100)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['battle', 'tick'], name='unique_tick')]
        indexes = [models.Index(fields=['battle', 'tick'])]


class Casualty(models.Model):
    battle = models.ForeignKey(Battle, on_delete=models.CASCADE, related_name='casualties')
    tick = models.IntegerField()
    sim_time = models.FloatField()
    attacker_id = models.CharField(max_length=24, null=True, blank=True)
    attacker_side = models.CharField(max_length=4, null=True, blank=True)
    attacker_name = models.CharField(max_length=60, null=True, blank=True)
    attacker_cls = models.CharField(max_length=10, null=True, blank=True)
    attacker_formation = models.CharField(max_length=8, null=True, blank=True)
    victim_id = models.CharField(max_length=24)
    victim_side = models.CharField(max_length=4)
    victim_name = models.CharField(max_length=60)
    victim_cls = models.CharField(max_length=10)
    victim_formation = models.CharField(max_length=8, null=True, blank=True)
    weapon_id = models.CharField(max_length=40, null=True, blank=True)
    weapon_name = models.CharField(max_length=60, null=True, blank=True)
    round = models.CharField(max_length=40, null=True, blank=True)
    range_m = models.FloatField(null=True, blank=True)

    class Meta:
        ordering = ['tick', 'id']


class BattleEvent(models.Model):
    battle = models.ForeignKey(Battle, on_delete=models.CASCADE, related_name='events')
    tick = models.IntegerField()
    type = models.CharField(max_length=16)
    data = models.JSONField()

    class Meta:
        indexes = [models.Index(fields=['battle', 'type'])]


class Platform(models.Model):
    id = models.CharField(primary_key=True, max_length=40)
    data = models.JSONField()
    updated = models.DateTimeField(auto_now=True)


class Weapon(models.Model):
    id = models.CharField(primary_key=True, max_length=40)
    data = models.JSONField()
    updated = models.DateTimeField(auto_now=True)


class OrbatNode(models.Model):
    LEVELS = [('LAND', 'Land'), ('FORCE', 'Force'), ('UNIT', 'Unit'), ('SUBUNIT', 'SubUnit')]
    CHILD = {'LAND': 'FORCE', 'FORCE': 'UNIT', 'UNIT': 'SUBUNIT', 'SUBUNIT': None}
    id = models.CharField(primary_key=True, max_length=16, default=short_id, editable=False)
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.CASCADE, related_name='children')
    level = models.CharField(max_length=8, choices=LEVELS)
    name = models.CharField(max_length=120)
    typology = models.CharField(max_length=60, blank=True)
    meta = models.JSONField(default=dict, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        constraints = [models.UniqueConstraint(fields=['parent', 'name'], name='unique_name_under_parent')]


class Player(models.Model):
    id = models.CharField(primary_key=True, max_length=16, default=short_id, editable=False)
    scenario = models.ForeignKey(Scenario, on_delete=models.CASCADE, related_name='players')
    side = models.CharField(max_length=4)
    slot = models.CharField(max_length=8, blank=True)
    callsign = models.CharField(max_length=24)
    token_hash = models.CharField(max_length=64, db_index=True)
    joined = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now=True)


class AdvisorLog(models.Model):
    battle = models.ForeignKey(Battle, on_delete=models.CASCADE, related_name='advisor_logs')
    side = models.CharField(max_length=5)
    tick = models.IntegerField()
    kind = models.CharField(max_length=20)  # threat | coa_applied | suggestion | briefing | ask
    content = models.JSONField()
    created = models.DateTimeField(auto_now_add=True)
