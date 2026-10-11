from django.contrib import admin

from . import models


@admin.register(models.Scenario)
class ScenarioAdmin(admin.ModelAdmin):
    list_display = ('name', 'room_code', 'theatre', 'status', 'red_mode', 'owner', 'updated')
    list_filter = ('status', 'theatre', 'red_mode')
    search_fields = ('name', 'room_code')


@admin.register(models.Battle)
class BattleAdmin(admin.ModelAdmin):
    list_display = ('id', 'scenario', 'run_no', 'status', 'outcome', 'current_tick', 'started', 'ended')
    list_filter = ('status', 'outcome')


@admin.register(models.Casualty)
class CasualtyAdmin(admin.ModelAdmin):
    list_display = ('battle', 'tick', 'attacker_name', 'victim_name', 'weapon_name', 'range_m')
    list_filter = ('victim_side', 'victim_cls')


for m in (models.Zone, models.ForceKey, models.Entity, models.HVT, models.Platform, models.Weapon, models.OrbatNode,
          models.Player, models.AdvisorLog):
    admin.site.register(m)
