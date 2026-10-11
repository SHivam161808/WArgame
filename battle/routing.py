from django.urls import re_path

from .consumers import BattleConsumer

websocket_urlpatterns = [re_path(r'^ws/battle/(?P<room_code>[A-Za-z0-9]{4,8})/$', BattleConsumer.as_asgi())]
