# Gameplay guide

## Objective
Find and destroy the enemy **HVT** (command post) while keeping yours intact. Destroying the enemy HVT wins
outright. If neither HVT falls before the time limit, the side with more kills wins; equal kills is a **DRAW**;
both HVTs destroyed is a **STALEMATE**. The battle also ends if one force is wiped out.

## Roles and rooms
- Each force has four formations: **ALPHA** (assault), **BRAVO** (HVT defence), **CHARLIE** (fire support),
  **DELTA** (reconnaissance). Each human player commands one; the first player of a force is its **Commander**
  (places the HVT, marks Ready, sets the battle pace and may re-task any formation of the force).
- Up to 4 humans per force. Every formation without a connected human is commanded by the AI. If a player
  disconnects, the AI takes over until they return.
- RED is the autonomous **AI Red player** unless the Exercise Controller selects HUMAN or MIXED.

## Commanding
| Action | How |
|---|---|
| Select a unit | click it on the map or in the order-of-battle list |
| Plot a formation route | select a unit → *Plot route* → click waypoints → double-click / Enter / right-click |
| Re-task a single unit | *Unit route*, *Halt*, *Rejoin* (back into formation) |
| Doctrine / shape | dropdowns in the selection panel (AGGRESSIVE, FLANKING, SUPPRESSIVE, DEFENSIVE, RECON, HOLD) |
| Indirect fire | select a CHARLIE unit → *Fire mission* → click the target area (8 rounds) |
| Spectator camera | *Spectate* (mouse wheel zooms, Esc returns to free camera) |
| Rewind while live | drag the timeline; *LIVE* returns — the battle keeps running |
| AI advisor | assessments arrive automatically; *Execute* applies a recommendation only after you confirm |

## Village Skirmish (PUBG-style mode)
A close-quarters squad battle on a **600 m × 600 m village**, generated fresh for every match: ~120 houses (huts,
two-room houses, big houses, open sheds) along a main road, a north–south road and lanes, a **government school**
in a walled compound with the tricolour, a lake, haystacks and mud walls for cover, electric poles, water tanks.
Doors and windows are real openings — walk in through doors, see and shoot through windows, walls stop bullets.

- **Squads of 10**, BLUE vs RED. Friends join either side with the Room ID and that side's key; every empty place
  is a **bot** that loots, patrols, hunts you, reacts to gunfire, takes cover-strafes, reloads and heals.
- Everybody starts with a **pistol**. Loot lies in the rooms: Vector-9 SMG, M416, AKM, SKS marksman rifle,
  AWM sniper, S12K shotgun, RPG-7, ammunition, medkits, body armour (vest) and helmets. A killed soldier drops a
  crate with everything they carried. Up to 3 weapons.
- **Vehicles stand parked and belong to nobody**: hatchbacks, motorbikes (one at each spawn), patrol jeeps with a
  machine gun and two abandoned tanks. **F** to drive, **F** to get out. Driving into enemies knocks them down.
- **No respawns** — when you are down you spectate your squad mates. Last squad standing wins; at the time limit
  (15 min) kills decide. Bullets pass through squad mates (no friendly fire).

| Key | Action |
|---|---|
| F | pick up the item at your feet / drive the vehicle next to you / get out |
| R | reload (magazines: e.g. 30 / 90) |
| H | use a medkit (+75 health, 4 s, interrupted if you are shot) |
| 1 – 3 or mouse wheel | switch weapon |
| hold right click | aim down sights — red dot; ×4 / ×8 scope on the SKS / AWM |
| M | full map with grid, squad and spotted enemies |
| Space (when down) | spectate the next squad mate |

Headshots do about double damage (helmets reduce it), vests absorb a third of body hits, damage falls off beyond
60 % of a weapon's range, crouching makes you smaller and steadier, sprinting and hip-fire spread your shots.

Start one from the Command Panel with **Village Skirmish** (or `POST /api/skirmish/`); the page opens the lobby
and shows the Room ID and both keys.

## Your soldier (on foot)
Every human player fights as their own soldier (Para SF trooper for BLUE, OPFOR ranger for RED). When the battle
starts you drop in on foot beside your HQ in third person; your squad mates are the other human players of your
force. Click the 3D view to lock the mouse.

| Key / mouse | Action |
|---|---|
| W A S D | move / strafe (relative to where you look) |
| Shift | sprint (≈21 km/h on firm ground; slower uphill, in sand, mud and snow) |
| C | crouch (quieter, steadier, slower) |
| Mouse | look / aim · **hold right click** to aim down sights (first person, red-dot; ×4 sight with the launcher) |
| Left click / Space | fire — X-95 assault rifle (3-round bursts, hold for auto) or MPATGM top-attack launcher |
| 1 / 2 | rifle / launcher |
| V | first ↔ third person |
| **F** | get into the friendly vehicle you are standing next to — you take the controls; **F** again gets out (aircraft: **G**) |
| **T** | call a ride: the nearest friendly AI ground vehicle drives to you and waits |
| Enter / Shift+Enter | team chat / all chat |
| N | night vision / thermal |
| P | (after Esc) back into your soldier |
| Esc | release the mouse (commander mode: map orders, *Take control* of any of your vehicles) |

Deep water makes you swim (slow, rifle stowed in the HUD speed panel as SWIM). If you are killed you respawn at
your HQ after 20 seconds; a vehicle that is destroyed with you inside throws you out wounded. Soldiers do not
count towards the force's surviving strength — the battle is still decided by the HVTs and the armoured forces.
If you disconnect your soldier leaves the field and returns where you were if you rejoin within two minutes.

The squad panel (bottom right) lists every human player of your force with health, range or the vehicle they
are in; squad mates are green squares with callsigns on the minimap.

## Direct control (drive & gun)
Get into a vehicle with **F** as a soldier, or (in commander mode) select one of your vehicles → **Take control**.
Click the 3D view to lock the mouse.

| Key / mouse | Action |
|---|---|
| W / S | throttle forward / reverse |
| A / D | steer (hull) |
| Mouse | traverse turret / elevate gun (sensitivity in the layers menu) |
| Left click / Space | fire (hold for autocannons) |
| **Hold right click** | aim down the gunner's sight (release to return) |
| **Hold Alt** | free-look around without moving the turret |
| V | toggle the gunner's scope |
| Mouse wheel | scope zoom ×2 · ×4 · ×8 · ×12, or camera distance outside the scope |
| C / Shift+C | camera: chase · far chase · hull chase · driver · commander's hatch · cinematic orbit · top-down |
| N | sensor: Day → Night vision → Thermal |
| 1 – 4 | select weapon |
| R / F | climb / descend (aircraft) |
| F (G in aircraft) | get out — only when your soldier is inside |
| Esc | release control (commander mode) |
| M | (outside a vehicle) 3D battle view ↔ tactical map |

Feedback: procedural 3D sound (engine, weapons, explosions with speed-of-sound delay, incoming cracks),
recoil and camera shake, hit markers with penetration results, kill confirmations, red damage-direction
arrows toward whoever is shooting you, a low-health vignette and a rotating minimap radar.

The HUD shows speed and the terrain speed cap, throttle, heading, slope and grade under the vehicle, ground type,
swimming, hull/fuel/morale gauges, ammunition and reload per weapon, APS charges and damage flags
(IMMOBILISED, WEAPONS DAMAGED, NO FUEL, SHAKEN). Guided missiles lock onto the detected enemy nearest the
crosshair; unguided rounds fly along the aim line with real dispersion.

## Terrain and physics
- **Slope:** a vehicle's speed comes from engine power against gravity, rolling resistance and drag. Climbs slow it,
  descents speed it up until the brakes cap it, and slopes steeper than the platform's limit stop it.
  Corridors (avenues of approach) are engineered roads: fast going, with switchback-limited grades.
- **Ground types:** road, farmland, sand, mud, snow, ice (low traction, slides), rock, salt pan (fast), marsh (slow).
- **Water:** amphibious IFVs/APCs swim, hover craft skim at speed, tanks are blocked by water deeper than their ford depth.
- **Altitude:** thin air above 1.5 km reduces engine power (Kargil–Dras).
- **Detection:** sensor range × light (night hides you unless the enemy has thermal) × weather × target signature
  × (1 − concealment). Moving and firing raise your signature, and dust raises it further in the desert. Line of sight
  is checked against the terrain with earth curvature.
- **Gunnery:** hit probability comes from weapon dispersion against the target's presented size, and falls with
  range, motion, poor crews, weather, darkness and concealment. Railguns are flat and fast; missiles can be stopped
  by APS or laser point-defence; artillery is area fire against top armour.
- **Damage:** penetration is compared with the armour facet hit (front/side/rear/top, with obliquity). A penetrating
  hit can cause a mobility kill, a firepower kill or a catastrophic kill. Morale drops with hits and nearby losses.
- **Logistics:** aircraft return to base to rearm and refuel; ground units near their own HVT slowly resupply.

## Theatres
| Theatre | Season | Biome | Weather | Character |
|---|---|---|---|---|
| Thar Desert — Longewala | Summer | DESERT | CLEAR, DUST, HAZE | Open dune sea with long sight lines. Sand slows tracked vehicles, every move kicks up a dust plume that betrays position, and midday heat shimmer degrades long-range gunnery. |
| Punjab Plains — Asal Uttar | Monsoon | MONSOON | RAIN, STORM, CLEAR, FOG | Flat, irrigated farmland soaked by the monsoon. Low ground turns to mud that bogs down heavy armour; rain cuts visibility and the corridors become the only fast going. |
| Kashmir LoC — Uri | Autumn | MOUNTAIN | CLEAR, FOG, RAIN | Steep forested ridges along the Line of Control. Valleys force armour onto a few corridors, ridgelines block sight lines and dead ground offers excellent concealment. Air and hover assets shine here. |
| Kargil–Dras — Winter Ice | Winter | ICE | SNOW, BLIZZARD, CLEAR | High-altitude snowfields above 3,000 m. Thin air cuts engine power, ice robs traction on slopes, cold raises fuel burn and blizzards reduce visibility to a few hundred metres. |
| Rann of Kutch — Coastal Marsh | Post-monsoon | MARSH | HAZE, FOG, CLEAR | Vast salt flats cut by tidal creeks and marsh. Hard salt pan is fast going, marsh is slow, and open water can only be crossed by amphibious and hover units. |

Time of day: Dawn, Day, Evening, Night, or Dynamic (a full day/night cycle every 24 sim-minutes).

## Platforms (BLUE / RED)
| Platform | Class | Armour F/S/R/T mm | Road/off/swim km/h | Grade | Sensor | Weapons |
|---|---|---|---|---|---|---|
| Vajra-X Railgun MBT / Typhoon-R Railgun MBT | MBT | 980/360/120/95 | 72/50 | 32° | 5.2 km TI | rail-120, atgm-top, hmg-127 |
| Bhishma-NG MBT / Scorpion-K MBT | MBT | 820/300/100/80 | 65/45 | 30° | 4.5 km TI | apfsds-125, heat-125, hmg-127 |
| Garuda Amphibious IFV / Viper Amphibious IFV | IFV | 260/90/45/30 | 75/52/9 | 30° | 4.2 km TI | ac-40, atgm-top |
| Kavach Wheeled APC / Jackal Wheeled APC | APC | 110/45/25/20 | 100/48/10 | 28° | 3.2 km TI | hmg-127 |
| Trishul Rail-SPG / Thunder 155 SPG | SPG | 70/35/25/25 | 62/38 | 26° | 2.5 km TI | rail-arty, hmg-127 |
| Pinaka-NG MLRS / Storm MLRS | MLRS | 40/25/20/15 | 85/40 | 24° | 2 km | mlrs-214 |
| Netra Hover Recon / Wraith Hover Recon | RECON | 45/22/15/10 | 115/95/70 | 24° | 9.5 km TI radar | ac-30 |
| Agni Hover Tank / Cobra Hover Tank | HOVER | 420/150/60/40 | 105/88/75 | 22° | 4.8 km TI | rail-90, hmg-127 |
| Akash-X Air Defence / Spear Air Defence | AD | 60/30/20/15 | 70/40 | 26° | 16 km TI radar | sam-sr, laser-pd |
| Rudra-X Attack Gunship / Hellcat Attack Gunship | GUNSHIP | 25/15/10/10 | 290/290 | 90° | 8 km TI radar | agm-hf, ac-30 |
| Tejas-NG Strike Fighter / Falcon Strike Fighter | JET | 15/10/10/8 | 950/950 | 90° | 12 km TI radar | glide-bomb |
| Drishti Recon UAV / Hawk Recon UAV | UAV | 2/2/2/2 | 180/180 | 90° | 11 km TI | atgm-lite |
| Brigade Command Post (HVT) / Corps Forward HQ (HVT) | HVT | 250/250/250/160 | 0/0 | 0° | 3.5 km TI radar | laser-pd |

## Weapons
| ID | Name | Type | Round | Range | Pen mm | Damage | Blast m | Traits |
|---|---|---|---|---|---|---|---|---|
| `rail-120` | 120 mm EM Railgun | RAILGUN | HV-Slug | 5.2 km | 950 | 72 | — |  |
| `rail-90` | 90 mm Light Railgun | RAILGUN | HV-Slug | 4.2 km | 620 | 55 | — |  |
| `apfsds-125` | 125 mm Smoothbore | KE | APFSDS | 4 km | 720 | 62 | — |  |
| `heat-125` | 125 mm Smoothbore | CE | Tandem-HEAT | 3.5 km | 760 | 55 | 4 |  |
| `atgm-top` | Top-Attack ATGM | MISSILE | Tandem top-attack | 6 km | 1100 | 88 | 5 | guided top-attack |
| `atgm-lite` | Light Loitering ATGM | MISSILE | EFP top-attack | 5 km | 700 | 60 | 4 | guided top-attack |
| `ac-40` | 40 mm CT Autocannon | AUTOCANNON | APFSDS-T / ABM | 2.6 km | 130 | 13 | — | anti-air |
| `ac-30` | 30 mm Chain Gun | AUTOCANNON | APDS-T | 2.2 km | 75 | 9 | — | anti-air |
| `hmg-127` | 12.7 mm RWS | AUTOCANNON | API-T | 1.6 km | 22 | 4 | — | anti-air |
| `laser-pd` | 50 kW Laser Point-Defence | LASER | Beam | 3 km | 12 | 7 | — | anti-air |
| `arty-155` | 155 mm Guided Howitzer | ARTILLERY | Guided HE | 32 km | 140 | 62 | 38 | indirect |
| `rail-arty` | Hypersonic Rail Artillery | ARTILLERY | HVP | 42 km | 180 | 72 | 26 | indirect |
| `mlrs-214` | 214 mm Guided Rockets | ARTILLERY | DPICM | 45 km | 90 | 46 | 48 | indirect |
| `sam-sr` | Short-Range SAM | SAM | IIR proximity | 12 km | 60 | 95 | 10 | guided anti-air |
| `agm-hf` | Air-to-Ground Missile | MISSILE | Tandem top-attack | 8 km | 1100 | 92 | 5 | guided top-attack |
| `glide-bomb` | Precision Glide Bomb | MISSILE | Penetrator HE | 14 km | 650 | 120 | 28 | guided top-attack |

All values are editable by the Exercise Controller under *Assets*; edits apply from the next battle start.
