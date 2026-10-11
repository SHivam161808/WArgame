# Architecture

```
Browser (HTML + Bootstrap + vanilla JS)
  CesiumJS globe ── terrarium terrain provider, Sentinel-2 imagery, draped canvas overlays
  BattleScene    ── glTF models, interpolation, bars/labels, effects, weather/time post-processing
  battle.js / setup.js / aar.js / console.js …
        │  REST (DRF, session or player token)        │  WebSocket (Channels, JSON)
        ▼                                             ▼
Django (Daphne ASGI) ───────────────────────────────────────────────────────────
  battle.api        REST endpoints (OpenAPI via drf-spectacular)
  battle.consumers  WebSocket protocol → Room methods
  battle.rooms      Room per room code: slots, commanders, AI fill-in, deployment validation,
                    readiness, BattleRunner thread (tick loop → persist → broadcast per audience)
  battle.services   terrain ingest/cache, catalogue snapshot, keys, readiness
  battle.models     PostgreSQL schema (scenarios … ticks, casualties, ORBAT)
        │ pure function calls
        ▼
sim/ (pure Python + NumPy, no Django)
  engine.Battle.step(): commands → AI → movement → detection → fire → projectiles → morale → end → frame
  physics, terrain (grid + LOS), terrarium (ingest), pathfind (A*, corridors), combat, ai_agent, advisor, llm
```

## Tick loop and data flow
1. The `BattleRunner` thread (one per live room) calls `Battle.step()` under `room.lock` every
   `cadence_ms` (Auto) or on demand (Step).
2. The full frame is gzip-compressed into a `Tick` row (batched every 20 ticks). Casualties, key events and HVT
   acquisition/destruction are written as rows.
3. `filter_frame()` builds one view per audience (BLUE, RED, ADMIN). Players never receive hostiles outside
   their detection envelope.
4. Views are sent to Channels groups `room_<code>_<audience>`. The runner thread hands the send to the ASGI
   server's event loop with `run_coroutine_threadsafe` (see `battle/loop.py`), which the in-memory layer needs.
5. Clients interpolate between the last two frames, so motion stays smooth at 4–10 ticks/s.

## Server-authoritative design
Clients send intent only (orders, deployment plans, drive input, aim point). The server checks every message
for role and scope: slot ownership, commander rights, own units only, zone containment, inventory limits.
All randomness comes from the battle's seeded RNG; iteration over sets is sorted, so a seed reproduces the
battle across processes.

## Persistence (PostgreSQL)
`Scenario`, `TerrainGrid` (zlib float32 elevation + corridors), `Zone`, `ForceKey`, `Battle` (run, seed,
opening plans, catalogue snapshot, result), `Entity`, `HVT`, `Tick` (frame + per-tick series), `Casualty`,
`BattleEvent`, `Platform`, `Weapon`, `OrbatNode`, `Player` (hashed tokens), `AdvisorLog`.

## Rendering performance lessons (keep these)
- Give every Cesium `Model` its **own** `modelMatrix` object. The property keeps a reference, so a shared scratch
  matrix stacks every model in one place.
- Create models with `environmentMapOptions: { enabled: false }`. Per-model dynamic cube maps cost about 20 ms each.
- Write `modelMatrix`, node matrices and label positions only when they change.
- Don't use ground-clamped polylines or entities for static map graphics; paint them into one draped canvas
  imagery layer. Keep at most two overlay layers visible.
- Sample terrain height for units yourself (`globe.getHeight`, cached and round-robin) instead of
  `HeightReference.CLAMP_TO_GROUND` on many moving primitives.
- Use `?debug=nounits,nomodels,norender,noenv,nooverlay,noquality,nolight` on any globe page to isolate costs;
  `tools/browser_check.py split|fps` measures them headlessly on the real GPU.
