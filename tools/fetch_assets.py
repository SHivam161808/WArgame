"""Download the CC0 art used by the ground-level battle renderer (Poly Haven, public domain).

Textures (1k PBR: albedo, OpenGL normal, AO/rough/metal), nature models (glTF) and sky HDRIs
go to webapp/static/{textures,props,hdri}; the player-soldier characters (Quaternius, CC0, via poly.pizza)
go to webapp/static/characters. Re-running skips files that already exist.
Usage: python tools/fetch_assets.py
"""
import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / 'webapp' / 'static'
API = 'https://api.polyhaven.com/files/'

TEXTURES = {  # game material → Poly Haven asset
    'sand': 'coast_sand_01', 'rock': 'rock_boulder_dry', 'gravel': 'dry_ground_rocks', 'grass': 'leafy_grass',
    'mud': 'brown_mud_02', 'snow': 'snow_02', 'cracked': 'mud_cracked_dry_03', 'road': 'gravel_road', 'marsh': 'brown_mud_03',
    'armor': 'metal_plate', 'wear': 'rusty_painted_metal',  # vehicle surface detail
}
MODELS = {
    'tree_fir': 'fir_tree_01', 'tree_broad': 'island_tree_02', 'tree_dead': 'dead_tree_trunk_02',
    'rock_boulder': 'boulder_01', 'rock_coast': 'coast_rocks_05', 'grass_tuft': 'grass_medium_01', 'shrub_dry': 'dead_quiver_trunk',
}
# The scans are film-quality (up to 7 M triangles); gltfpack (MIT, meshoptimizer) makes them game-ready.
SIMPLIFY = {'tree_fir': 0.0012, 'tree_broad': 0.008, 'rock_coast': 0.004, 'rock_boulder': 0.04, 'tree_dead': 0.04,
            'grass_tuft': 0.25, 'shrub_dry': 0.2}
# Quaternius CC0 characters: SWAT body + animations (player soldiers); the soldier pack supplies the rifle mesh
CHARACTERS = {'swat': 'https://static.poly.pizza/713f6535-f4f3-4367-a4c6-ced126ae0936.glb',
              'soldier': 'https://static.poly.pizza/1083c1d3-d1d4-4682-adf6-bc516d06ac84.glb'}
HDRIS = {'day': 'citrus_orchard_puresky', 'evening': 'belfast_sunset_puresky', 'night': 'clarens_night_02',
         'overcast': 'approaching_storm', 'mountain': 'drakensberg_solitary_mountain_puresky'}


def get_json(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'ai-dssw'}), timeout=30) as r:
        return json.loads(r.read())


def download(url, dest: Path):
    if dest.exists() and dest.stat().st_size > 0:
        return 'cached'
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={'User-Agent': 'ai-dssw'})
    with urllib.request.urlopen(req, timeout=120) as r:
        dest.write_bytes(r.read())
    return 'ok'


def jobs():
    out = []
    for name, aid in TEXTURES.items():
        f = get_json(API + aid)
        out.append((f['Diffuse']['1k']['jpg']['url'], ROOT / 'textures' / f'{name}_diff.jpg'))
        out.append((f['nor_gl']['1k']['jpg']['url'], ROOT / 'textures' / f'{name}_nor.jpg'))
        out.append((f['arm']['1k']['jpg']['url'], ROOT / 'textures' / f'{name}_arm.jpg'))
    for name, aid in MODELS.items():
        if (ROOT / 'props' / f'{name}.glb').exists():
            continue
        g = get_json(API + aid)['gltf']['1k']['gltf']
        out.append((g['url'], ROOT / 'props' / name / f'{name}.gltf'))
        for rel, inc in g.get('include', {}).items():
            out.append((inc['url'], ROOT / 'props' / name / rel))
    out.append(('https://raw.githubusercontent.com/mrdoob/three.js/r170/examples/textures/waternormals.jpg',
                ROOT / 'textures' / 'waternormals.jpg'))  # MIT, three.js examples
    for name, url in CHARACTERS.items():
        out.append((url, ROOT / 'characters' / f'{name}.glb'))
    for name, aid in HDRIS.items():
        h = get_json(API + aid)['hdri']['1k']['hdr']
        out.append((h['url'], ROOT / 'hdri' / f'{name}.hdr'))
    return out


def main():
    todo = jobs()
    with ThreadPoolExecutor(8) as pool:
        for (url, dest), res in zip(todo, pool.map(lambda j: download(*j), todo)):
            print(res, dest.relative_to(ROOT))
    import shutil
    import subprocess
    for name, ratio in SIMPLIFY.items():
        src = ROOT / 'props' / name / f'{name}.gltf'
        if src.exists():
            subprocess.run(['npx', '-y', 'gltfpack@1.3.0', '-i', str(src), '-o', str(ROOT / 'props' / f'{name}.glb'),
                            '-si', str(ratio), '-sa', '-c'], check=True)
            shutil.rmtree(src.parent)
    total = sum(p.stat().st_size for p in ROOT.rglob('*') if p.is_file() and p.parts[-3:-1][0] in ('textures', 'props', 'hdri', 'characters'))
    print(f'done — {len(todo)} files, {total / 1e6:.0f} MB')


if __name__ == '__main__':
    main()
