# Interfaces

Interactive REST documentation: **`/api/docs/`** (Swagger UI); machine-readable schema: **`/api/schema/`**.
Administrators authenticate with a Django session (POST `/api/auth/login/`, then send `X-CSRFToken`).
Players get a token from `/api/join/` and send it as `X-Player-Token` (REST) or in the `join` message (WebSocket).

## REST overview

| Area | Endpoints |
|---|---|
| Auth | `POST auth/login/` · `POST auth/logout/` · `GET auth/me/` |
| Reference | `GET meta/` (theatres, weather, time of day, doctrines, shapes, classes, ground types) |
| Scenarios | `GET/POST scenarios/` · `GET/PATCH/DELETE scenarios/{id}/` · `GET scenarios/{id}/battles/` |
| Terrain | `POST scenarios/{id}/terrain/ingest/` (`mode: real\|flat`) · `GET …/terrain/` · `GET …/terrain/layer.png?layer=relief\|ground\|slope` · `GET …/terrain/sample/?x=&y=[&x2=&y2=]` (point query + LOS) |
| Zones | `GET/PUT/DELETE scenarios/{id}/zones/{BLUE\|RED}/` (`polygon: [[x,y],…]`, `method: vertices\|lasso`) |
| Inventory | `GET/PUT scenarios/{id}/inventory/` (`{BLUE: {platform: n} \| null, RED: …}`) |
| Force keys | `GET/POST scenarios/{id}/keys/` (`red_mode: AI\|HUMAN\|MIXED`) |
| Readiness & room | `GET scenarios/{id}/readiness/` · `POST …/open/` · `POST …/close/` · `POST …/briefing/` |
| Tick progression | `POST scenarios/{id}/control/` (`action: start\|pause\|resume\|step\|auto\|cadence\|end`, `value`) |
| Join | `POST join/` (`room_code, force_key, callsign`) → `{token, side, battle_id}` |
| Battles | `GET battles/{id}/` · `GET …/ticks/?from=&to=&step=` · `GET …/kills/` · `GET …/entities/` · `GET …/events/?type=` |
| Analytics | `GET battles/{id}/analytics/` (KPIs + chart series) · `POST battles/{id}/ask/` (`question`) |
| Assets | `GET/POST assets/{platforms\|weapons}/` · `GET/PUT/DELETE assets/{kind}/{id}/` · `POST assets/reset/` |
| Force hierarchy | `GET/POST orbat/` (`?level=&parent=`) · `PATCH/DELETE orbat/{id}/` |

Coordinates are metres on the scenario's local plane (x east, y north) centred on `scenario.origin`
(lat/lon). Headings are compass degrees.

## WebSocket protocol — `/ws/battle/{ROOM_CODE}/`

Every message is JSON `{"type": …}`. Requests may carry `"rid"`; the server answers
`{"type":"reply","rid":…,"ok":true|false, …}`.

### Client → server
| type | fields | who |
|---|---|---|
| `join` | `token` | player (first message) |
| `admin_watch` | — | owner admin (session cookie) |
| `claim_slot` | `slot` (ALPHA…DELTA) | player |
| `deploy_plan` | `plan {formations:[{id,name,doctrine,shape,composition,anchor,facing}], hvt}` (+`side` for admin) | commander / own slot |
| `ready` | `ready` (+`side` for admin) | commander / admin |
| `suggest` | (+`side` for admin) | AI formation suggestion |
| `cmd` | `cmd {type: route\|doctrine\|shape\|halt\|fireMission\|rejoin, formation?, unit_ids?, waypoints?, …}` | slot owner / commander |
| `cmd` (soldier) | `cmd {type: enter\|exit\|summon}` — board the nearest friendly vehicle, get out, call a ride | any player |
| `cmd` (skirmish) | `cmd {type: pick, item?}` · `{type: reload}` · `{type: heal}` | any player |
| `possess` | `unit_id` or `null` (own soldier always allowed) | slot owner / commander |
| `drive` | `unit_id, throttle, steer, turret_yaw, gun_pitch, fire, weapon, aim [x,y,z], climb` (≈20 Hz); soldiers add `strafe, sprint, crouch` | possessing player |
| `chat` | `text` (≤200 chars), `all` (true = both forces) | player |
| `control` | `action` start\|pause\|resume\|step\|auto\|cadence, `value` | commander / admin |
| `fog` | `enabled` | admin |
| `advisor_request` / `advisor_apply` | — / `coa_id` | player (admin: request only) |
| `play_again` | — | commander / admin, after the end |
| `history` | `from, to` (≤ 400 ticks) | everyone (fog-filtered) |

### Server → client
| type | payload |
|---|---|
| `session` | scenario, theatre, zones, corridors, terrain meta/stats, inventory, catalogue, briefing |
| `lobby` | status, slots (callsign/commander/connected), readiness, own plans, pace (auto, cadence), tick |
| `frame` | TickFrame filtered for the audience: `units[]` (compact keys), `projectiles[]`, `events[]`, `formations[]` (own), `detected`, `hvt_acquired`, `kills`, `status`, `hour` |
| `advisor` | threat assessment: headline, risk, bears_on, tempo, detail, 3 ranked COAs with executable orders |
| `ended` | result: outcome, reason, kills, deployed/lost/surviving, HVT integrity |
| `rerun` | new battle id after Play Again |
| `chat` | `from` (callsign), `side`, `all`, `text`, `t` (sim time) — team chat goes to the own force + controller |

Unit view keys: `id s f p c n x y z h pi ro v ty gp hp mhp m fu fm am rl st mk fk d tk tg gr sw sl gd th k aps alt hvt ctl cap rt`
(side, formation, platform, class, name, position, heading/pitch/roll, speed, turret yaw, gun pitch, health, morale,
fuel, ammo, reloads, status, mobility/firepower kill, doctrine, task, target, ground, swimming, slope, grade,
throttle, kills, APS charges, altitude, is-HVT, controller, speed cap, route). Player soldiers add
`av` (true), `mt` (vehicle they sit in), `rs` (respawn countdown), `stn` (STAND/CROUCH), `pl` (player id); a vehicle
driving to collect a soldier carries `pk` (player id). Enemy units omit ammo, fuel, reload, target, doctrine, task,
route, `pl` and `pk`.

Village Skirmish frames also carry `mode: "SKIRMISH"`, `alive {BLUE, RED}` and — when loot changed, and every
50 ticks — `items: [[id, kind, x, y, z], …]`. Soldiers carry `wp` (weapons), `cw` (weapon in hand), `mg`
(rounds in each magazine), `rlt` (magazine change left), `ht` (medkit left), `md` (medkits), `ve` / `hl` (vest,
helmet) and `bot`. Empty vehicles have side `NEUT` and are visible to everybody. The map layout (roads, lake,
buildings, every wall piece) is in the `village` key of the `heights.bin` metadata.
`POST /api/skirmish/ {theatre?, time_of_day?, weather?}` creates a new village, opens the room and joins the
caller as BLUE (returns `room_code`, `keys`, `play_url`); `POST /api/scenarios/{id}/quickplay/` on a skirmish
scenario generates a fresh village for the next match.

Event types: `FIRE`, `IMPACT`, `KILL`, `PICK`, `INTERCEPT`, `DETECT`, `HVT_ACQUIRED`, `INTRUSION` (with composition),
`ALERT`, `END`.
