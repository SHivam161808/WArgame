# AI-DSSW Armoured Battle Console — Consolidated Requirements

Stack (mandated by the product owner): **Django (Python) backend, PostgreSQL, HTML/CSS/Bootstrap frontend**,
tick-based Python simulation. See `SKILL.md`.

Source: `Team Capability_Project_Scenario (2).pdf` (sections a–q) **plus** the product owner's
additional requirements (marked **[PO]**). Every requirement has an ID so code, tests and
reviews can trace back to it. Status of each item is tracked in the traceability matrix at the end.

> **Hard constraint (C-1):** the whole solution — engine, globe, DB, charts, every library — is
> free and open-source software. No paid, licensed, proprietary or subscription component.

---

## 1. Scenario & Objective

| ID | Requirement |
|----|-------------|
| SC-1 | A BLUE armoured battle group deploys on real-geography 3D terrain in the India–Pakistan border region to find and destroy the RED High-Value Target (HVT) while protecting its own. |
| SC-2 | The battle is resolved by a **tick-based simulation engine** accounting for elevation, slope, line of sight, sensor ranges, mobility, fuel, terrain-aware ballistics and hit probability. |
| SC-3 | Every tick is recorded in full for live rewind, complete replay and post-battle analytics. |
| SC-4 | End conditions (any one): either HVT destroyed; all BLUE destroyed; all RED destroyed; time limit reached. |
| SC-5 | Winner: enemy HVT destroyed wins outright. If neither HVT destroyed → more confirmed kills wins; equal → **DRAW**. Both HVTs destroyed → **STALEMATE**. |

## 2. Players, Teams & Rooms

| ID | Requirement |
|----|-------------|
| PL-1 | **Exercise Controller (Administrator)** creates scenarios, sets duration and tick size, ingests terrain, draws zones, sets inventories, chooses RED mode, generates force keys, watches live, reviews replay/results. |
| PL-2 | **BLUE** is always the human team. Four formations: **ALPHA** (Assault, tank heavy), **BRAVO** (HVT defence ring), **CHARLIE** (Fire support / indirect fire), **DELTA** (Recon, long-range sensors). Composition configurable within the admin inventory. |
| PL-3 | **RED (OPFOR)** default is a fully autonomous **AI Red player**: auto-generated order of battle placed at random inside the RED zone, each unit given a weighted doctrine (Aggressive / Flanking / Suppressive / Defensive) and an avenue of approach. Runs unattended for the whole battle. |
| PL-4 **[PO]** | **Rooms of up to 4 + 4 humans.** Each battle is a room with a short **Room ID**. Players join with Room ID + force key + callsign. Each human claims one formation slot (ALPHA/BRAVO/CHARLIE/DELTA) of their side. The first human on a side is that side's **Commander** (places HVT, marks Ready). |
| PL-5 **[PO]** | **AI fill-in.** Any formation slot without a human is commanded by the computer (same AI agent as RED). If a human disconnects, AI takes over the slot; if they reconnect, they get it back. |
| PL-6 | RED modes selectable by admin: `AI` (PROJECT default, PDF-compliant), `HUMAN`, `MIXED` (humans + AI fill). RED Human is the PDF's "future capability"; it is implemented but AI mode must always work alone. |
| PL-7 | **AI formation suggestion** for BLUE: composition, shape and doctrine from inventory, zones and terrain. Player can accept, adjust or disregard. Human keeps command authority. |

## 3. Theatres, Seasons, Time of Day **[PO]**

| ID | Requirement |
|----|-------------|
| TH-1 | Area of operations is a **large** real-geography sector of the India–Pakistan border (default 36 × 36 km, configurable up to 80 × 80 km). |
| TH-2 | **Five theatres / seasons**, each with distinct terrain, ground type, weather and gameplay effect:<br>1. **Thar Desert — Summer** (Longewala, Rajasthan): sand, dust trails, heat haze.<br>2. **Punjab Plains — Monsoon Rain** (Asal Uttar / Khem Karan): mud, rain, reduced visibility, flooded low ground.<br>3. **Kashmir LoC — Mountain Autumn** (Uri sector): steep slopes, ridgelines, high concealment.<br>4. **Kargil–Dras — Winter Ice** (Ladakh): snow and ice, low traction, cold fuel penalty, blizzard.<br>5. **Rann of Kutch — Coastal Marsh** (Sir Creek): salt flats, tidal water, amphibious crossings, sea fog. |
| TH-3 | **Time of day**: Dawn, Day, Evening, Night, and Dynamic (clock advances with simulated time). Night reduces optical detection; thermal sights are unaffected. |
| TH-4 | Weather affects detection range, dispersion, traction and fuel use, and is visible (rain, snow, dust, fog rendered on screen). |

## 4. Terrain & Environment (PDF §d)

| ID | Requirement |
|----|-------------|
| TR-1 | Open-source 3D globe engine (CesiumJS, Apache-2.0, loaded in the HTML page) with terrain, satellite imagery, national border overlay, numbered coordinate grid; camera opens framed on the AO. |
| TR-2 | Admin draws BLUE and RED polygon zones by clicking vertices **or** freeform lasso; each erasable and redrawable. |
| TR-3 | Real elevation ingested into a fine grid; per cell: elevation, slope, class (flat / sloped / steep), mobility score, concealment score (+ ground type, water depth). |
| TR-4 | Vehicles move and turn by the actual slope below them, slow on steep ground, and cannot climb beyond their gradient limit. |
| TR-5 | No terrain data → fall back to flat, unobstructed going. |
| TR-6 | Auto-generated **mobility corridors** (avenues of approach) between zones give road-like speed near them; off-road is slower. |
| TR-7 | Concealment reduces detection range and hit chance. LOS is computed live against terrain, with earth-curvature (and refraction) correction. |

## 5. Platforms, Weapons & Physics **[PO + PDF §j]**

| ID | Requirement |
|----|-------------|
| PW-1 | Configurable platform & weapon repository editable from the admin UI with no code changes. Edits take effect at the next battle start/reset (engine cache). |
| PW-2 | Platform data: name, class (MBT / IFV / APC / SPG / RECON + futuristic **HOVER**, **GUNSHIP**, **JET**, **UAV**, **AD**, **MLRS**), armour per facet (front/side/rear/top), dimensions, mass, power, on/off-road speed, gradient limit, fuel capacity & burn, amphibious/swim speed, ford depth, fire rate, sensor range, thermal, crew proficiency, active protection. |
| PW-3 | Weapon data: type (KE, CE, AUTOCANNON, ARTILLERY, **MISSILE**, **RAILGUN**, **LASER**), max effective range, dispersion, muzzle velocity, penetration, damage, blast radius, ammo per load, reload time, guidance. |
| PW-4 **[PO]** | **Futuristic arsenal**: railgun MBTs, hover tanks, active protection, laser point-defence, top-attack missiles, guided artillery, attack gunships, strike jets, recon drones, air-defence vehicles, missile launchers. |
| PW-5 **[PO]** | **Mobility physics**: speed falls going uphill and rises going downhill (power-to-weight, gravity, rolling resistance, traction per ground type). Water: amphibious units swim at swim speed, others ford shallow water or are blocked. Aircraft ignore slope but burn fuel faster and can be shot by AD. |
| PW-6 **[PO]** | **Damage model**: penetration vs effective armour (facet + impact angle); outcomes: no-pen, damaged, mobility kill (immobilised), firepower kill, destroyed. Morale drops with damage and nearby losses. |
| PW-7 | Hit probability from dispersion vs presented target size, range, motion, crew skill, concealment, weather. |

## 6. Direct Control, Scopes & HUD **[PO]**

| ID | Requirement |
|----|-------------|
| DC-1 | A player can **take direct control** of any unit in their own formation: drive (W/S throttle, A/D steer), traverse turret (mouse), fire (click), switch weapon (1–4). Server-authoritative; same physics as AI units. |
| DC-2 | **Scope view**: gunner sight with zoom levels (×2/×4/×8/×12), reticle with range ladder, laser rangefinder, Day / Night-vision / Thermal modes. |
| DC-3 | **HUD** always shows: speed (km/h), throttle, heading compass, slope under vehicle, ground type, swimming indicator, fuel, ammo per weapon, reload bar, HP, subsystem damage, morale, immobilised indicator, mini-map, kill feed. |
| DC-4 | Rendering is smooth: client interpolates between server ticks; target 60 fps on integrated GPUs with adaptive quality (resolution scale, shadows, effects). |
| DC-5 | Every human player fights as their own soldier: third/first person, walk/sprint/crouch/swim with slope and ground effects, rifle and portable missile launcher, aim down sights; boards any friendly vehicle within reach (F) and takes its controls, gets out (F/G), calls a ride (T); respawns at the HQ 20 s after death. Squad mates (other humans of the force) are shown in a squad panel and on the minimap; team and all chat. |
| DC-6 | **Village Skirmish mode**: a procedural 600 m village (houses with enterable rooms, doors and windows, a school compound, roads, a lake, cover), squads of up to 4 humans per side with bots filling every empty place, pistol start and lootable weapons / ammo / medkits / armour, parked cars, bikes, jeeps and tanks anyone can drive, no respawns (spectate), last squad standing wins. Walls block movement, sight and bullets on the server. |

## 7. Plans & Actions (PDF §e–g)

| ID | Requirement |
|----|-------------|
| BP-1 | BLUE: join with force key; build formations by name, doctrine, shape, composition; deploy facing the RED zone. |
| BP-2 | Place BLUE HVT inside the BLUE zone; mark Ready; battle starts when both sides are ready. |
| BP-3 | Select formation, plot waypoint route, set doctrine; advance tick by tick or continuously on Auto. |
| BP-4 | DELTA's sensors detect RED units in range and LOS and reveal the RED HVT when discovered. |
| BP-5 | ALPHA direct fire; CHARLIE indirect area fire; any formation/unit re-tasked live, incl. click a unit to redirect mid-simulation; unit list, intrusion alerts, spectator camera. |
| BP-6 | HVT reduced to zero → win; results pop-up, dashboard and replay automatically; **Play Again** restarts from the same opening positions. |
| RP-1 | RED AI spawns its own OOB at random in RED zone; weighted doctrines; avenue of approach. |
| RP-2 | RED AI builds its own detection picture every tick and dispatches nearby units to intercept BLUE intruders inside its zone. |
| RP-3 | RED AI behaviour by doctrine — hold & fire, stand-off, flank at offset, close to effective range — re-evaluated continuously; Aggressive/Flanking advance on BLUE HVT, Defensive/Suppressive hold near RED HVT. |
| RP-4 | Engagement auto-selects best target and round per unit. |

## 8. Live Simulation (PDF §h)

| ID | Requirement |
|----|-------------|
| LS-1 | Tick loop, step or continuous at configurable cadence; every tick resolved, timestamped, persisted. |
| LS-2 | Live 3D unit models geo-positioned and oriented to terrain; force affiliation obvious. |
| LS-3 | Live health & morale bars over each unit; ammo and fuel telemetry; immobilised indicator. |
| LS-4 | Fire visualisation: tracers along line of fire, muzzle flash, impact (fireball, smoke, debris) on hit, dust on miss. |
| LS-5 | Click-to-command any formation at any time without pausing the engine. |
| LS-6 | HVT markers with health/morale once acquired; destroyed icon; unacquired HVT hidden from the opposing force. |
| LS-7 | Fog of war (admin-controlled): each player sees only hostiles in own force's detection envelope. **Enforced server-side.** |
| LS-8 | Intrusion & contact alerts with composition breakdown. |
| LS-9 | Spectator chase camera attachable to any entity showing heading, status, surroundings. |
| LS-10 | Rewind while live via timeline, without disturbing the engine; return to live. |

## 9. AI Tactical Advisor (PDF §i)

| ID | Requirement |
|----|-------------|
| AI-1 | Live threat assessment at configurable tick interval or on demand: most significant risk, formation it bears on, tempo. |
| AI-2 | Three ranked courses of action with rationale; advisory only — executed only after operator confirmation. |
| AI-3 | **Ask the Battle**: natural-language Q&A over tick logs, kill feed and casualties; answers cite recorded data. |
| AI-4 | Formation suggestion for BLUE (see PL-7). |
| AI-5 | RED is an adaptive AI agent; battles from the same start can differ. |
| AI-6 | AI-generated mission briefing before the battle: force balance, terrain features, likely axis of contact. |
| AI-7 | LLM backend must be FOSS: local **Ollama** / llama.cpp / any OpenAI-compatible self-hosted endpoint. A built-in deterministic analyst runs when no LLM is configured, so every AI feature always works offline. |

## 10. Admin, Inventory, ORBAT, Access (PDF §k, o, p)

| ID | Requirement |
|----|-------------|
| AD-1 | Command panel: create scenario (name, tick interval, time limit, description), live status + progress per scenario, open / replay / delete; admins see only their own scenarios. |
| AD-2 | Tick management: sim-seconds per tick set at creation; total ticks or time ceiling ends the battle automatically. |
| AD-3 | Guided tabbed setup: Terrain · Zones · Inventory · Readiness · Force Keys · Replay. |
| AD-4 | Live monitoring of both sides; Fog-of-War control; layer toggles (zones, grid, borders, annotations) at any time. |
| OB-1 | Order-of-battle register: Lands → Forces (by typology) → Units → SubUnits, independent of scenarios; counts per tier; view selector; add/edit/remove; duplicate names under one parent rejected. |
| AC-1 | Admin: staff credential (Django auth, PBKDF2 password hashing, session login + CSRF). |
| AC-2 | Force player: limited to one force of one scenario via unique force key; sees only own zone/inventory; with fog sees only detected hostiles. |
| AC-3 | Every action validated server-side for role and scope; standard web safeguards (CSRF, security headers, input validation, throttling). |

## 11. Visualisation, AAR & Architecture (PDF §m, n, q)

| ID | Requirement |
|----|-------------|
| VZ-1 | Terrain-conformant 3D vehicles with health/morale bars, labels, tracer/impact effects, distinct HVT markers. |
| VZ-2 | Routes as waypoint traces; labelled force-coloured zones. |
| VZ-3 | 2D/3D toggle, zoom, rotate, tilt, location search, adaptive numbered grid, terrain-precise picking. |
| AR-1 | Full replay of every tick with the same 3D view and effects; variable speed; step forward/back; preset cameras. |
| AR-2 | Kill feed: tick, attacker force & entity, victim force & entity, weapon/round. |
| AR-3 | Results dashboard: outcome banner + rationale; KPIs (deployed, lost, surviving, kills per side); charts: force strength over time, comparative force assessment, attrition by class, HVT health over time, kill ratio; AI briefing. |
| SA-1 | Backend: **Django 5.2 (Python)** + Django Channels (WebSockets) + **PostgreSQL** relational DB. |
| SA-2 | Frontend fully in the browser: **HTML + CSS + Bootstrap 5** templates with vanilla JS, CesiumJS globe, Chart.js charts; no client install. |
| SA-3 | Documented API (DRF + drf-spectacular, OpenAPI at `/api/schema/`, Swagger UI at `/api/docs/`) covering scenarios, ticks, entities/formations, objectives/zones, terrain, analytics, assets, ORBAT. |
| SA-4 | Queryable records for scenario, force key, terrain grid, zone, entity, HVT, tick, casualty, asset, ORBAT. |

---

## Resolved conflicts & assumptions

1. **RED human vs PDF "RED must be AI".** The PDF requires AI-controlled RED for the PROJECT and lists
   RED Human as a future capability. The PO wants humans on both sides. Resolution: RED mode
   defaults to `AI`; `HUMAN` and `MIXED` are selectable extensions. AI mode is the acceptance path.
2. **"Blue is always real players."** BLUE needs at least one human (the Commander) to start a battle
   (admins can override with "AI vs AI demo" for testing). Empty BLUE slots are AI-filled (PL-5).
3. **4 players per team ↔ 4 formations.** Each human commands exactly one formation; the
   Commander can also order AI-filled formations of their own side.
4. **Real-time feel vs tick engine.** The engine is still strictly tick-based (SC-2). Small ticks
   (0.1–0.25 s) with continuous Auto cadence give real-time driving; the client interpolates.
5. **Imagery.** Default imagery is EOX Sentinel-2 cloudless 2016 (CC-BY 4.0). OpenStreetMap is
   offered as a second layer. Elevation: AWS Terrain Tiles (Mapzen terrarium, open data).
   Borders: Natural Earth (public domain), India point-of-view edition.
6. **LLM.** C-1 rules out paid APIs. AI features use a self-hosted FOSS model when configured and a
   built-in rule-based analyst otherwise.

## Traceability matrix

| Area | Requirement IDs | Implemented in |
|------|-----------------|----------------|
| Tick engine | SC-2..5, LS-1, AD-2 | `sim/engine.py` |
| Terrain | TR-3..7 | `sim/terrain.py`, `sim/terrarium.py`, `sim/pathfind.py` |
| Physics | PW-5..7, TR-4 | `sim/physics.py`, `sim/combat.py` |
| AI agents | PL-3, PL-5, RP-1..4, AI-5 | `sim/ai_agent.py` |
| Advisor | AI-1..4, AI-6, AI-7 | `sim/advisor.py`, `sim/llm.py` |
| Rooms / auth | PL-4..6, AC-1..3 | `battle/rooms.py`, `battle/consumers.py` |
| Persistence | SC-3, SA-4 | `battle/models.py`, `battle/rooms.py` (BattleRunner) |
| REST API | SA-3 | `battle/api.py` |
| Globe & effects | TR-1, LS-2..4, VZ-1..3 | `webapp/static/js/globe.js`, `scene.js`, `effects.js`, `environment.js` |
| Direct control, soldiers, skirmish | DC-1..6 | `webapp/static/js/hud.js`, `battle.js`, `ground/soldier.js`, `sim/engine.py` (avatars), `sim/village.py`, `sim/skirmish.py`, `ground/village.js` |
| Admin UI | AD-1..4, TR-2, PW-1, OB-1 | `webapp/templates/console/*`, `static/js/console.js`, `setup.js`, `assets.js`, `orbat.js` |
| AAR | AR-1..3, AI-3 | `webapp/templates/aar.html`, `static/js/aar.js` |
