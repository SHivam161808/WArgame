"""WebSocket protocol (SKILL.md §6). Every message is JSON ``{"type": ..., "rid"?: ...}``.

Requests that carry an ``rid`` get a ``{"type": "reply", "rid": ..., "ok": bool, ...}`` answer.
Players authenticate with the token returned by POST /api/join/; administrators with their
Django session (staff, owner of the scenario).
"""
from __future__ import annotations

import hashlib
import logging

from asgiref.sync import async_to_sync
from channels.generic.websocket import JsonWebsocketConsumer

from .models import Player, Scenario
from .rooms import RoomError, group_name, registry

log = logging.getLogger('battle')


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class BattleConsumer(JsonWebsocketConsumer):
    def connect(self):
        self.code = self.scope['url_route']['kwargs']['room_code'].upper()
        self.room = None
        self.player_id = None
        self.audience = None
        self.accept()

    def disconnect(self, close_code):
        if self.audience:
            async_to_sync(self.channel_layer.group_discard)(group_name(self.code, self.audience), self.channel_name)
        if self.room and self.player_id:
            self.room.detach(self.player_id, self.channel_name)

    def relay(self, event):
        self.send(text_data=event['text'])

    def reply(self, rid, ok=True, **kw):
        if rid is not None:
            self.send_json(dict(type='reply', rid=rid, ok=ok, **kw))

    def receive_json(self, msg, **kwargs):
        typ = msg.get('type')
        rid = msg.get('rid')
        try:
            handler = getattr(self, f'on_{typ}', None)
            if handler is None:
                raise RoomError(f'unknown message type {typ!r}')
            if typ not in ('join', 'admin_watch') and self.audience is None:
                raise RoomError('join the room first')
            out = handler(msg) or {}
            self.reply(rid, True, **out)
        except RoomError as e:
            self.reply(rid, False, error=str(e))
        except (KeyError, ValueError, TypeError) as e:
            self.reply(rid, False, error=f'bad request: {e}')
        except Exception as e:  # never kill the socket on a handler bug
            log.exception('ws handler %s failed', typ)
            self.reply(rid, False, error='server error')

    # ── authentication ─────────────────────────────────────────────────────
    def _enter(self, audience: str):
        self.audience = audience
        async_to_sync(self.channel_layer.group_add)(group_name(self.code, audience), self.channel_name)

    def on_join(self, msg):
        token = str(msg.get('token', ''))
        player = Player.objects.filter(token_hash=token_hash(token), scenario__room_code=self.code).first()
        if player is None:
            raise RoomError('invalid or expired player token — join again with the force key')
        room = registry.get(self.code)
        if room is None:
            raise RoomError('this room is not open')
        room.attach(player, self.channel_name)
        self.room, self.player_id = room, player.id
        self._enter(player.side)
        self.send_json(dict(type='session', session=room.session_info(player.side), player_id=player.id))
        self.send_json(dict(type='lobby', lobby=room.lobby_state(player.side)))
        if room.frames:
            from sim.engine import filter_frame
            self.send_json(dict(type='frame', frame=room.join_frame(player.side)))
        return dict(player_id=player.id, side=player.side)

    def on_admin_watch(self, msg):
        user = self.scope.get('user')
        sc = Scenario.objects.filter(room_code=self.code).first()
        if not (user and user.is_authenticated and user.is_staff and sc and (sc.owner_id == user.id or user.is_superuser)):
            raise RoomError('administrator login required')
        room = registry.get(self.code)
        if room is None:
            raise RoomError('open the lobby first')
        self.room = room
        self._enter('ADMIN')
        self.send_json(dict(type='session', session=room.session_info('ADMIN')))
        self.send_json(dict(type='lobby', lobby=room.lobby_state('ADMIN')))
        if room.frames:
            from sim.engine import filter_frame
            self.send_json(dict(type='frame', frame=room.join_frame('ADMIN')))
        return {}

    @property
    def is_admin(self):
        return self.audience == 'ADMIN'

    def _player(self):
        if not self.player_id:
            raise RoomError('players only')
        return self.player_id

    # ── lobby & deployment ─────────────────────────────────────────────────
    def on_claim_slot(self, msg):
        self.room.claim_slot(self._player(), msg['slot'])

    def on_deploy_plan(self, msg):
        if self.is_admin:
            self.room.set_plan(None, msg['side'], msg['plan'])
        else:
            pid = self._player()
            self.room.set_plan(pid, self.room.players[pid]['side'], msg['plan'])

    def on_ready(self, msg):
        if self.is_admin:
            self.room.set_ready(None, msg['side'], bool(msg.get('ready', True)))
        else:
            pid = self._player()
            self.room.set_ready(pid, self.room.players[pid]['side'], bool(msg.get('ready', True)))

    def on_suggest(self, msg):
        side = msg.get('side') if self.is_admin else self.room.players[self._player()]['side']
        return dict(suggestion=self.room.suggest(side))

    # ── battle ─────────────────────────────────────────────────────────────
    def on_cmd(self, msg):
        self.room.command(self._player(), msg['cmd'])

    def on_possess(self, msg):
        self.room.possess(self._player(), msg.get('unit_id'))

    def on_drive(self, msg):
        if self.player_id:
            self.room.drive(self.player_id, msg)

    def on_chat(self, msg):
        self.room.chat(self._player(), msg.get('text', ''), bool(msg.get('all')))

    def on_control(self, msg):
        self.room.control(None if self.is_admin else self._player(), msg['action'], msg.get('value'))

    def on_fog(self, msg):
        if not self.is_admin:
            raise RoomError('only the Exercise Controller can change fog of war')
        self.room.set_fog(bool(msg.get('enabled')))

    def on_advisor_request(self, msg):
        side = msg.get('side', 'BLUE') if self.is_admin else self.room.players[self._player()]['side']
        return dict(assessment=self.room.advise(side))

    def on_advisor_apply(self, msg):
        self.room.apply_coa(self._player(), msg['coa_id'])

    def on_play_again(self, msg):
        return dict(battle_id=self.room.play_again(None if self.is_admin else self._player()))

    def on_history(self, msg):
        return dict(frames=self.room.history(self.audience, msg['from'], msg['to']))

    def on_ping(self, msg):
        return {}
