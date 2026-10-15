# AI-DSSW — Armoured Battle Console

A browser wargame on real India–Pakistan border terrain: futuristic armoured battle groups,
4 v 4 rooms where the AI fills every empty slot, a tick-based Python simulation (terrain slope,
line of sight with earth curvature, sensors, fuel, ballistics, armour penetration), direct
vehicle control with gunner scopes, and an AI Tactical Advisor. Every tick is stored in
PostgreSQL for live rewind, full 3D replay and AI-assisted after-action review.

| | |
|---|---|
| Backend | Django 5.2 · Django Channels (WebSockets) · Daphne · Django REST Framework |
| Database | PostgreSQL (project-local cluster or any server via `DATABASE_URL`) |
| Simulation | Pure Python + NumPy in `sim/` (no Django imports) |
| Frontend | HTML · CSS · Bootstrap 5 · vanilla JS · **Three.js ground-level 3D battle view** · CesiumJS globe (map) · Chart.js |
| Art | Poly Haven CC0 PBR textures, scanned trees/rocks, sky HDRIs; Quaternius CC0 animated soldiers (`tools/fetch_assets.py`) |
| Data | AWS Terrain Tiles (elevation), Sentinel-2 cloudless (imagery), Natural Earth (borders, India view + LoC) |

Everything is free and open-source (requirement C-1). Requirements: [`docs/REQUIREMENTS.md`](docs/REQUIREMENTS.md).
Contributor/AI guide: [`SKILL.md`](SKILL.md).

## Quick start with Docker (Windows, Linux, macOS)

Install Docker Desktop (Windows/macOS) or Docker Engine (Linux), then in this folder:

```bash
docker compose up -d --build    # PostgreSQL + the game; first build takes a few minutes
```

Open http://localhost:8000 and sign in as `admin` / `admin123`. No database setup: PostgreSQL runs in its own
container with a named volume. Step-by-step guide with troubleshooting: [`docs/Docker_Setup_Guide.pdf`](docs/Docker_Setup_Guide.pdf)
(rebuild it with `tools/make_docker_guide.py`).

## Quick start (Linux)

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
tools/pg_local.sh init          # project-local PostgreSQL on 127.0.0.1:5433 (no sudo); skip if you use your own server
cp .env.example .env            # DATABASE_URL, ADMIN_USER, ADMIN_PASSWORD …
.venv/bin/python manage.py migrate
.venv/bin/python manage.py seed --demo     # admin user, catalogue, ORBAT, one demo scenario per theatre
.venv/bin/python tools/fetch_assets.py     # CC0 textures, trees/rocks, sky HDRIs for the 3D view (~55 MB, needs npx)
tools/dev.sh                    # starts PostgreSQL + http://localhost:8000 (HTTP and WebSockets)
```

Using an existing PostgreSQL instead: create a database and set
`DATABASE_URL=postgres://user:password@host:5432/dbname` in `.env`.

## Playing

**Village Skirmish (quickest):** sign in at `/login/`, press **Village Skirmish** on the Command Panel. A new
village is generated and the lobby opens with the Room ID and both force keys — friends join from `/` (BLUE or RED,
up to 4 a side, bots fill the rest). Press READY. Loot, drive, fight; last squad standing wins.
See [`docs/GAMEPLAY.md`](docs/GAMEPLAY.md#village-skirmish-pubg-style-mode).

**Armoured battle:**

1. **Exercise Controller** — sign in at `/login/` (default `admin` / `admin123`, change via `ADMIN_PASSWORD`).
   Command Panel → *Setup* a scenario: ingest terrain, draw BLUE/RED zones (vertices or lasso), set inventory,
   generate force keys, then *Open lobby*. Watch everything from *Live monitor* (fog-of-war control, layers).
2. **Players** — open `/`, enter the Room ID, force key and a callsign. Up to 4 players per force; each takes a
   formation slot (ALPHA assault, BRAVO HVT defence, CHARLIE fire support, DELTA recon). The first player is the
   Commander. Empty slots are played by the AI. RED is the AI Red player by default (or HUMAN / MIXED).
3. **Deploy** — build formations within the inventory (or accept the AI suggestion), place them and the HVT inside
   your zone, press READY. The battle starts when both forces are ready.
4. **Fight** — you drop in as **your own soldier** beside your HQ (third person, WASD + mouse, Shift sprint,
   C crouch, right-click aim, F to take the controls of any friendly tank/IFV/aircraft you stand next to,
   T to call a ride, Enter for team chat). Your squad mates are the other players of your force. Press Esc for
   commander mode: plot routes, change doctrine/shape, call CHARLIE fire missions, spectate, **Take control** of
   any of your vehicles, or press **M** for the tactical map. See [`docs/GAMEPLAY.md`](docs/GAMEPLAY.md).
5. **Review** — results pop-up, then the AAR page: 3D replay, kill feed, charts and *Ask the Battle*.

## Commands

| Command | Purpose |
|---|---|
| `tools/dev.sh` | start PostgreSQL + dev server (Daphne) on :8000 |
| `python -m pytest` | engine unit tests (`tests/`) |
| `python manage.py test battle` | API / rooms / WebSocket integration tests (PostgreSQL) |
| `python manage.py simulate --theatre kutch-rann --ticks 3600` | headless AI-vs-AI battle on real terrain |
| `python tools/ai_battle.py thar-longewala` | full AI-vs-AI battle through the live server, prints the AAR link |
| `python tools/smoke_flow.py` | API smoke test: open a room and join as BLUE |
| `python tools/browser_check.py fps\|split\|shot\|soldier\|skirmish` | headless GPU browser checks (Playwright); `soldier` plays a two-player squad, `skirmish` a village match |
| `python tools/gen_models.py` | regenerate the 3D unit models (`webapp/static/models/*.glb`) |
| `python tools/fetch_borders.py` | rebuild `webapp/static/data/borders.geojson` |

API documentation: `/api/docs/` (Swagger UI), schema at `/api/schema/`, WebSocket protocol in [`docs/API.md`](docs/API.md).

## Production notes

- Run `python manage.py collectstatic`, serve with `daphne -b 0.0.0.0 -p 8000 config.asgi:application` behind a
  reverse proxy (nginx) that forwards WebSockets; set `DEBUG=0`, `SECRET_KEY`, `ALLOWED_HOSTS`,
  `CSRF_TRUSTED_ORIGINS`, `HTTPS=1`.
- Battles run in-process: use **one** Daphne process per host (rooms live in memory). For multiple processes, add
  `channels-redis` + `REDIS_URL` and pin each room to a process.
- Optional AI text from a self-hosted model: set `LLM_BASE_URL` (OpenAI-compatible, e.g. Ollama
  `http://localhost:11434`) and `LLM_MODEL`. Without it the built-in analyst produces every advisor output.
