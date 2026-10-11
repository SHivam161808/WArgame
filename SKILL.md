---
name: ai-dssw-wargame
description: Build, extend and debug the AI-DSSW Armoured Battle Console — a Django + Bootstrap browser wargame on real India–Pakistan border terrain with a tick-based Python simulation engine, 4v4 rooms with AI fill-in, CesiumJS 3D globe, AI Red agent and AI Tactical Advisor. Use for any change to the engine, rooms, admin console, battle UI, replay/AAR, assets or docs in this repo.
---

# AI-DSSW Armoured Battle Console — Project Skill

Company capability-test project. Requirements live in `docs/REQUIREMENTS.md` (every
requirement has an ID such as `SC-4`, `PL-5`, `DC-2`; cite IDs in commits, tests and PRs).
The original brief is `Team Capability_Project_Scenario (2).pdf`.

## 1. Mandated stack (do not change without the product owner)

| Layer | Technology | Notes |
|---|---|---|
| Backend | **Django 5.2 (Python 3.10+)** | apps: `battle`, `webapp`; pure-Python engine in `sim/` |
| Realtime | **Django Channels 4 + Daphne** (WebSockets) | in-memory channel layer for one process; Redis layer (FOSS) to scale |
| API | Django REST Framework + drf-spectacular | OpenAPI at `/api/schema/`, Swagger UI at `/api/docs/` |
| Database | **PostgreSQL** (`DATABASE_URL`; `tools/pg_local.sh` runs a project-local cluster on :5433) | relational, all records queryable; SQLite only for quick experiments |
| Simulation | Python + NumPy, tick-based, server-authoritative | `sim/` has **no Django imports** (unit-testable) |
| Frontend | **HTML + CSS + Bootstrap 5** Django templates, vanilla JS (ES modules) | no React/Node build step |
| 3D battle view | **Three.js** (MIT) ground-level renderer in `webapp/static/js/ground/` | PBR texture splatting on real heights, CC0 Poly Haven art |
| Map / globe | CesiumJS (Apache-2.0), loaded in the HTML page | terrain from AWS Terrain Tiles (open data); deployment, routes, AAR |
| Charts | Chart.js (MIT) | AAR dashboard |
| AI/LLM | optional self-hosted FOSS model (Ollama / llama.cpp, OpenAI-compatible URL) | built-in rule-based analyst when no LLM |

**Hard rule C-1:** everything is free and open-source. Never add a paid API, licensed
library or subscription service (no Cesium ion token, no Google/Mapbox keys, no paid LLM).

## 2. Game in one paragraph

An Exercise Controller (admin) builds a scenario on one of five real border theatres, draws
BLUE and RED zones, sets inventories and generates a **Room ID + force keys**. Up to **4 humans
per team** join; each human commands one formation — ALPHA (assault), BRAVO (HVT defence),
CHARLIE (fire support), DELTA (recon). **Every empty slot is played by the computer.** BLUE is
always the human team; RED defaults to a fully autonomous AI Red agent (PDF requirement) and can
be HUMAN or MIXED. The battle is a tick simulation: terrain slope, LOS with earth curvature,
sensors, fog of war, fuel, ballistics, hit probability and armour penetration. **Every human also
fights as their own soldier** (third/first person, rifle + launcher, sprint/crouch/swim, F to take the
controls of any friendly vehicle, T to call a ride, team chat, respawn at the HQ). Destroy the enemy
HVT to win. A second mode, **Village Skirmish** (`Scenario.mode = 'SKIRMISH'`), is a PUBG-style 4 v 4
in a procedural 600 m village: loot, parked vehicles, bots fill empty places, no respawns. Every tick is stored for live rewind, full replay and the AI-assisted AAR.

## 3. Repository map

```
config/            Django settings, urls, asgi (Channels routing)
sim/               PURE PYTHON ENGINE — no Django imports
  geo.py           local metres <-> lat/lon, bearings, RNG, erf, polygons
  theatres.py      5 theatres, weather, time of day, ground classification
  inventory.py     default futuristic platforms & weapons, default order of battle
  physics.py       ground table, vehicle dynamics, hit probability, penetration
  terrain.py       TerrainGrid (elevation, slope, class, ground, mobility, concealment, LOS)
  terrarium.py     elevation tile ingest (AWS Terrain Tiles) + flat fallback
  pathfind.py      NavGrid A* with per-vehicle nav profiles, corridor generation
  formations.py    formation shapes → slot offsets
  engine.py        Battle: state + step() pipeline
  combat.py        targeting, firing, projectiles, damage, morale
  ai_agent.py      AI commander (RED and AI-filled BLUE slots)
  advisor.py       threat assessment, 3 ranked COAs, briefing, formation suggestion, Ask-the-Battle
  llm.py           optional OpenAI-compatible client (self-hosted FOSS model)
battle/            Django app: models, services, REST API, WebSocket consumer, runner thread
  models.py        Scenario, TerrainGrid, Zone, ForceKey, Battle, Entity, HVT, Tick, Casualty,
                   BattleEvent, Platform, Weapon, OrbatNode, Player, AdvisorLog
  rooms.py         room registry, slot claims, AI fill-in, readiness, BattleRunner thread
  loop.py          captures the ASGI event loop so runner threads can broadcast thread-safely
  consumers.py     WebSocket protocol (JSON messages, see §6)
  api.py           REST endpoints (DRF)
webapp/            Django app: page views, templates, static
  templates/       base.html (Bootstrap), home, admin console, scenario setup, play, aar
  static/js/       api.js, geo.js, globe.js, scene.js, effects.js, environment.js, hud.js,
                   battle.js (play + live monitor), console.js, setup.js, assets.js, orbat.js, aar.js
  static/css/      theme.css
  static/models/   generated .glb unit models (tools/gen_models.py)
  static/data/     borders.geojson (Natural Earth, India POV + LoC)
tools/             gen_models.py, fetch_borders.py, pg_local.sh, dev.sh, smoke_flow.py,
                   ai_battle.py, browser_check.py
tests/test_sim.py  engine unit tests (pytest) · battle/tests.py  integration tests (manage.py test)
docs/              REQUIREMENTS.md, ARCHITECTURE.md, API.md, GAMEPLAY.md
```

## 4. Engine invariants — never break these

1. **Server-authoritative.** Clients send *intent* (routes, doctrines, drive input, aim point).
   Only `sim/` moves units, rolls dice or applies damage.
2. **Fog of war is enforced server-side.** The runner builds one frame per audience
   (BLUE, RED, ADMIN). A player's socket never receives undetected enemy units or an
   unacquired enemy HVT. Do not "hide it in the UI".
3. **Determinism per run.** All randomness goes through the battle's seeded RNG
   (`battle.rng`). Same seed + same inputs = same battle. Play Again uses a new seed and the same
   opening positions.
4. **Tick pipeline order** (in `Battle.step`): commands → AI decisions → movement → detection →
   targeting & fire → projectiles → damage & morale → end conditions → record frame.
5. **Every tick is persisted** (`Tick` row: gzip JSON of the full unfiltered frame) plus
   `Casualty` and `BattleEvent` rows. Replays and AAR read only from the database.
6. **Catalogue snapshot.** Platform/weapon edits apply on the next battle start or reset; a
   running battle uses the snapshot stored on its `Battle` row.
7. **Units:** metres, seconds, degrees. x = east, y = north, z = metres ASL. Heading = compass
   (0 = north, clockwise). The AO origin is the theatre centre on the border/LoC.
8. **`sim/` imports nothing from Django.** Keep it testable with plain `python -m pytest`.

## 5. Simulation rules (keep code and docs consistent)

- **Terrain grid**: default 36×36 km AO, 50 m cells. Per cell: elevation, slope (°), class
  (FLAT < 6°, SLOPED < 18°, STEEP), ground type, mobility score, concealment score, water depth.
  No elevation data → flat, unobstructed fallback (TR-5).
- **Corridors**: three A* avenues between zone centroids; cells within ~2× corridor width count
  as ROAD (on-road speed).
- **Mobility** (`physics.step_mobility`): tractive force = min(P/v, μ·m·g·cosθ) against gravity
  m·g·sinθ, rolling resistance Crr·m·g·cosθ and drag. So vehicles slow uphill, speed up downhill
  (capped, with slip on ice), and stop above `max_grade_deg`. Thin air above 1.5 km cuts power.
  Water deeper than `ford_depth_m`: amphibious units swim at `swim_kph`, hover craft skim, others are
  blocked. Aircraft ignore slope and fly at cruise altitude AGL.
- **Detection**: sensor range × light (night 0.3 unless thermal) × weather visibility × target
  signature × (1 − concealment·0.6) × movement/firing bloom; then LOS against terrain with earth
  bulge d1·d2/(2·k·R), k = 4/3.
- **Hit probability**: gaussian dispersion σ = mrad/1000 · range · motion · skill · weather · light,
  P = erf(w/(2√2σ))·erf(h/(2√2σ)) on the presented box, salvo-combined, × (1 − concealment·0.45).
  Guided weapons use a seeker probability, then APS / laser point-defence / AD can intercept.
- **Penetration**: facet (front ≤ 40°, rear ≥ 145°, otherwise side; top for top-attack/artillery),
  effective armour = facet / cos(obliquity); KE loses penetration with range. Outcomes: NO_PEN,
  DAMAGED, MOBILITY_KILL (immobilised), FIREPOWER_KILL, DESTROYED.
- **Morale** 0–100: drops on hits, nearby friendly losses and HVT loss; < 30 shaken (accuracy
  down, AI withdraws), < 10 broken (retreats).
- **End & winner** (SC-4/5): HVT destroyed → that side loses outright; both HVTs → STALEMATE;
  otherwise more kills wins, equal kills → DRAW.
- **Player soldiers** (`Unit.avatar`, class `INF`, platforms `b-/r-inf-trooper`): spawned per connected
  player at battle start / on mid-battle join (`Battle.spawn_avatar`, beside the own HVT, own RNG stream so
  joins never perturb the battle RNG). On foot: walk 2.0, sprint 5.8, crouch 1.0, swim 0.9 m/s × ground factor
  × uphill penalty; >110 % grade impassable. `enter` boards the nearest own non-HVT vehicle within reach
  (soldier `mounted_in`, vehicle `controller = player`); `exit`/destruction puts the soldier back beside it.
  Dead soldiers respawn at the HQ after 20 s. Soldiers are excluded from AI planning, end conditions, results
  and vehicle separation; mounted soldiers are excluded from detection, targeting and blast. `summon` sends the
  nearest free AI ground vehicle (`Unit.pickup`) to the soldier; it waits ~60 s. Disconnect removes the soldier
  (`remove_avatar`); rejoining within 120 s restores position and health.

- **Village Skirmish** (`sim/village.py` map + geometry, `sim/skirmish.py` rules + bots): one seed → roads,
  lake, buildings, props, vehicles, loot, spawns (`village.generate`), terrain at 2 m cells with flat building pads
  and land clamped ≥ 0.6 m above the lake level (`terrain_elevation`; water only in the lake). Every wall piece
  `[ax, ay, bx, by, zb, z0, z1, kind]` is shared by server and client: movement collision (`Walls.collide`,
  pieces with z0 < 1 m block), LOS / bullets (`Walls.ray`, height-aware so windows are see-through), bots'
  waypoint graph (`NavGraph`: open-ground grid + door inside/outside + rooms). `Skirmish(Battle)` overrides the
  geometry hooks on `Battle` (`wall_ray`, `wall_clear`, `collide_move`, `ground_z`, `vehicle_blocked`,
  `after_move`, `pick_item`), detection (LOS through walls; footsteps < 6 m, shots < 60 m), outcome (last squad
  standing / kills), and runs `BotSquad` per side through the same drive inputs a human sends. Small arms
  (`type SMALLARMS`): `mag`, `reload_mag_s`, `reload_s` = time between shots, `auto`, `pellets`, `zoom`, `head`;
  `combat._ray_shot` does dispersion, pellets, walls, head/body boxes and `area_blast`. Empty vehicles are side
  `NEUT`, controller `PARKED`; bots are controller `BOT` (rooms never reset those). No friendly fire.

## 6. WebSocket protocol (`/ws/battle/<room_code>/`)

Client → server `{"type": ..., ...}`: `join` (force_key, callsign, token?), `admin_watch`,
`claim_slot`, `deploy_plan`, `ready`, `suggest`, `cmd` (route | doctrine | shape | halt |
fireMission | rejoin | enter | exit | summon), `possess`, `drive` (throttle, steer, strafe, sprint,
crouch, turret_yaw, gun_pitch, fire, weapon, aim), `chat` (text, all), `control` (start | pause |
resume | step | auto | cadence), `advisor_request`, `advisor_apply`, `fog`, `play_again`,
`history` (from, to).

Server → client: `session`, `lobby`, `frame` (filtered TickFrame), `advisor`, `ended`, `toast`,
`rerun`, `chat`, `reply` (answer to a request, carries the request `rid`).

## 7. Frontend conventions

- Bootstrap 5 dark theme + `static/css/theme.css`. Templates extend `base.html`. No inline
  `<style>` blocks except page-specific tweaks.
- JS is vanilla ES modules under `static/js/`, no bundler. Cesium, Bootstrap and Chart.js come
  from jsDelivr (versions pinned in `base.html`); set `VENDOR_LOCAL=1` to serve local copies.
- The client **interpolates** between the last two frames (render delay ≈ 1 tick), giving
  smooth 60 fps motion even at 5 ticks/s.
- Direct control (DC-1..3): `W/S` throttle, `A/D` steer, mouse = turret, click = fire, `1-4`
  weapons, `Z` zoom steps (×2/×4/×8/×12), `N` cycles Day/NV/Thermal, `V` scope on/off,
  `Esc` releases the vehicle. The HUD always shows speed, slope, ground, swim state, fuel, ammo,
  reload, HP, damage, morale and the immobilised flag.
- Soldier mode (same `DriveController`, `inf = true`): WASD relative to the look direction, Shift sprint,
  C crouch, V first/third person, hold RMB = first-person ADS, F enter/exit, T call a ride, Enter chat.
  The controller follows the player's soldier into and out of vehicles (`bind()`), and the view predicts the
  own soldier's position client-side (`GroundView.setLocalMove`) and converges on the server position.

## 8. Common tasks

- **Run**: `tools/pg_local.sh init` once, then `tools/dev.sh` (starts PostgreSQL, migrates, serves HTTP + WebSockets on :8000). First time: `python manage.py seed --demo`. Default admin `admin` / `admin123` (`ADMIN_PASSWORD`).
- **Tests**: `python -m pytest` (engine, 41 tests incl. village/skirmish) and `python manage.py test battle` (API, rooms, WebSocket + fog enforcement on PostgreSQL).
- **End-to-end**: `python tools/ai_battle.py <theatre>` runs a full battle through the live server; `python tools/browser_check.py fps|split|shot|soldier` checks the 3D client headlessly on the real GPU (`soldier` plays two squad mates: sprint, ADS, chat, call a ride, get in and out).
- **Headless battle**: `python manage.py simulate --theatre thar-longewala --ticks 3000` runs AI vs AI and prints the result.
- **Add a platform or weapon**: edit it in Admin → Assets (no code), or change the defaults in
  `sim/inventory.py` and run `python manage.py seed --reset-assets`. A new 3D model also needs an
  entry in `tools/gen_models.py`.
- **Add a theatre**: append a `Theatre` to `sim/theatres.py` (centre on the border/LoC,
  `blue_bearing` toward India, biome rules, weather list). Nothing else is needed.
- **Regenerate assets**: `python tools/gen_models.py` (unit .glb files) and
  `python tools/fetch_borders.py` (borders GeoJSON).

## 9. Rendering rules (hard-won — do not regress)

1. Every Cesium `Model` gets its **own** `modelMatrix` object (the property stores a reference).
2. Models are created with `environmentMapOptions: { enabled: false }` (per-model cube maps kill the frame rate).
3. Only write model/node matrices and label positions when the value changed.
4. Static map graphics (zones, corridors, grid, borders) are painted into one draped canvas overlay — never
   ground-clamped polylines/entities. At most two overlay imagery layers visible at once.
5. Ground units sample `globe.getHeight` (cached, round-robin) — no `CLAMP_TO_GROUND` on moving primitives.
6. Broadcasts from background threads go through `battle/loop.py` (thread-safe hand-off to the ASGI loop).
7. Iterate sets in sorted order inside `sim/` (string hash order differs per process → breaks determinism).

8. **Three.js view** (`ground/`): props from gltfpack are *quantized* — always dequantize (`toFloat`) before
   baking transforms; never draw big instanced trees/rocks into the shadow map; bloom threshold stays HDR (≈4)
   so the sky does not bloom; data textures are row 0 = south, canvas textures are top row = north;
   steep faces use triplanar rock; units sit on `terrain.height()` (same function as the mesh).
10. **Village** (`ground/village.js`): walls/roofs/floors are merged into a few world-space-textured meshes
   (procedural canvas textures, no extra downloads); keep the JS collision / `rayT` identical to `sim/village.py`
   (prediction, camera pull-in and the crosshair ray use them); boarding reach must match `Skirmish._enter_vehicle`
   (`len/2 + 3 m`). Loot and in-hand weapons reuse the soldier pack's gun meshes (`kit.guns` / `kit.hand`).
9. **Soldiers** (`ground/soldier.js`): skinned clones need `SkeletonUtils.clone`; GLTFLoader sanitises node names
   (`Wrist.R` → `WristR`) so look bones up by both; the rifle's hand transform is *solved* in the arm-forward pose
   (barrel → +Z, magazine down) — the soldier pack's own transform is for a different rig and points the gun at
   the ground. Characters come from `tools/fetch_assets.py` (Quaternius CC0).

## 10. Definition of done for any change

- Requirement IDs referenced; `docs/REQUIREMENTS.md` traceability updated if scope changed.
- Engine change → unit test in `tests/` (physics, LOS, winner rules, AI behaviour).
- Fog-of-war and role checks still enforced server-side (test with a RED key).
- Runs on PostgreSQL (and stays portable — no vendor-specific SQL).
- Client change → `tools/browser_check.py fps` still ≈ bare-Cesium frame rate (see `docs/ARCHITECTURE.md` performance rules).
- No non-FOSS dependency added (C-1).
