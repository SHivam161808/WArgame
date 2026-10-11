"""Build webapp/static/data/borders.geojson from Natural Earth (public domain).

India point-of-view country outlines plus the de facto Line of Control / Siachen frontier,
clipped to the India–Pakistan theatre.  Usage: python tools/fetch_borders.py
"""
import json
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NE = 'https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/'
SOURCES = {
    'countries': (NE + 'ne_10m_admin_0_countries_ind.geojson', ROOT / 'data/cache/ne_ind.geojson'),
    'lines': (NE + 'ne_10m_admin_0_boundary_lines_land.geojson', ROOT / 'data/cache/ne_lines.geojson'),
}
OUT = ROOT / 'webapp/static/data/borders.geojson'
BBOX = (64, 20, 84, 38)
KEEP = {'IND', 'PAK', 'CHN', 'AFG', 'NPL'}


def load(name):
    url, path = SOURCES[name]
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        print('downloading', url)
        urllib.request.urlretrieve(url, path)
    return json.loads(path.read_text(encoding='utf8'))


def in_box(p):
    return BBOX[0] <= p[0] <= BBOX[2] and BBOX[1] <= p[1] <= BBOX[3]


def line(coords, props):
    return {'type': 'Feature', 'properties': props, 'geometry': {'type': 'LineString', 'coordinates': coords}}


def main():
    features = []
    for f in load('countries')['features']:
        a3 = f['properties']['ADM0_A3']
        if a3 not in KEEP:
            continue
        g = f['geometry']
        polys = [g['coordinates']] if g['type'] == 'Polygon' else g['coordinates']
        for poly in polys:
            for ring in poly:
                run = []
                for p in ring + [None]:
                    if p is not None and in_box(p):
                        run.append([round(p[0], 5), round(p[1], 5)])
                        continue
                    if len(run) > 1:
                        features.append(line(run, {'kind': 'boundary', 'country': a3, 'name': f['properties']['NAME']}))
                    run = []
    for f in load('lines')['features']:
        p = f['properties']
        pair = f"{p['ADM0_LEFT']} {p['ADM0_RIGHT']}"
        if not (re.search('Line of control|Indeterminant', p['FEATURECLA'], re.I) and re.search('India|Pakistan|Siachen', pair)):
            continue
        g = f['geometry']
        parts = [g['coordinates']] if g['type'] == 'LineString' else g['coordinates']
        for part in parts:
            coords = [[round(q[0], 5), round(q[1], 5)] for q in part if in_box(q)]
            if len(coords) > 1:
                features.append(line(coords, {'kind': 'loc', 'name': p['NAME'] or p['FEATURECLA']}))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({'type': 'FeatureCollection', 'attribution': 'Natural Earth (public domain), India POV', 'features': features}))
    print(f'wrote {len(features)} border lines -> {OUT.relative_to(ROOT)} ({OUT.stat().st_size // 1024} KB)')


if __name__ == '__main__':
    main()
