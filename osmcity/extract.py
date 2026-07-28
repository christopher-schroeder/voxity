"""Read an .osm.pbf and pull out everything inside a lon/lat box."""

import hashlib
import os
import pickle
import time

import numpy as np
import osmium

from . import tags as T
from .geo import Projection, clip_polygon, clip_polyline

KEYS = ('building', 'building:part', 'highway', 'railway', 'waterway',
        'natural', 'landuse', 'leisure', 'amenity', 'man_made',
        'area:highway', 'aeroway')

CACHE_VERSION = 8


class Scene:
    """Everything we extracted, already projected into local metres."""

    def __init__(self, bbox_ll, projection, extent):
        self.bbox_ll = bbox_ll
        self.projection = projection
        self.extent = extent          # (minx, minz, maxx, maxz) in metres
        self.buildings = []
        self.surfaces = []
        self.lines = []
        self.trees = np.zeros((0, 2), dtype=np.float32)

    def stats(self):
        return (f'{len(self.buildings)} buildings, {len(self.surfaces)} areas, '
                f'{len(self.lines)} ways, {len(self.trees)} trees')


def _ring_points(ring):
    return np.array([(n.lon, n.lat) for n in ring], dtype=np.float64)


def _way_points(way):
    try:
        return np.array([(n.lon, n.lat) for n in way.nodes], dtype=np.float64)
    except osmium.InvalidLocationError:
        return None


def _bbox_of(pts):
    return (pts[:, 0].min(), pts[:, 1].min(), pts[:, 0].max(), pts[:, 1].max())


def _hit(pts, bbox):
    return not (pts[:, 0].max() < bbox[0] or pts[:, 0].min() > bbox[2] or
                pts[:, 1].max() < bbox[1] or pts[:, 1].min() > bbox[3])


# Materialising every ring of a whole-city extract in Python is the slow part,
# so reject far-away features from one node before touching the rest. Big
# features (relations, very long ways) skip the shortcut and get the real test.
COARSE_MARGIN = 0.035         # degrees, ~2.3 km east-west / 3.9 km north-south
BIG_FEATURE = 120             # node count above which we don't take shortcuts


def _far(lon, lat, bbox):
    return not (bbox[0] - COARSE_MARGIN <= lon <= bbox[2] + COARSE_MARGIN and
                bbox[1] - COARSE_MARGIN <= lat <= bbox[3] + COARSE_MARGIN)


def _coarse_reject_area(area, bbox):
    if not area.from_way():
        return False
    try:
        ring = next(iter(area.outer_rings()))
        n = len(ring)
        if n > BIG_FEATURE:
            return False
        a = ring[0]
        if not _far(a.lon, a.lat, bbox):
            return False
        b = ring[n // 2]
        return _far(b.lon, b.lat, bbox)
    except (StopIteration, IndexError, RuntimeError):
        return True


def _coarse_reject_way(way, bbox):
    nodes = way.nodes
    n = len(nodes)
    if n == 0:
        return True
    if n > BIG_FEATURE:
        return False
    try:
        for i in (0, n // 2, n - 1):
            loc = nodes[i].location
            if not loc.valid() or not _far(loc.lon, loc.lat, bbox):
                return False
    except Exception:                                   # noqa: BLE001
        return False
    return True


def extract(path, bbox, verbose=True):
    """Extract features from `path` intersecting `bbox` = (w, s, e, n)."""
    minlon, minlat, maxlon, maxlat = bbox
    proj = Projection(0.5 * (minlon + maxlon), 0.5 * (minlat + maxlat))
    corners = proj.forward([minlon, maxlon], [minlat, maxlat])
    extent = (corners[:, 0].min(), corners[:, 1].min(),
              corners[:, 0].max(), corners[:, 1].max())
    scene = Scene(bbox, proj, extent)

    # Buildings poking in from just outside the box still belong in the frame.
    pad = ((maxlon - minlon) * 0.02, (maxlat - minlat) * 0.02)
    outer_bbox = (minlon - pad[0], minlat - pad[1], maxlon + pad[0], maxlat + pad[1])

    t0 = time.time()
    fp = (osmium.FileProcessor(path)
          .with_areas()
          .with_locations()
          .with_filter(osmium.filter.KeyFilter(*KEYS))
          # only trees are interesting as nodes; drop the rest in C++
          .with_filter(osmium.filter.KeyFilter('natural')
                       .enable_for(osmium.osm.NODE)))

    trees = []
    for obj in fp:
        if obj.is_area():
            _area(obj, scene, bbox, outer_bbox, proj)
        elif obj.is_way():
            _way(obj, scene, bbox, proj)
        elif obj.is_node():
            tg = obj.tags
            if tg.get('natural') == 'tree':
                loc = obj.location
                if (bbox[0] <= loc.lon <= bbox[2] and bbox[1] <= loc.lat <= bbox[3]):
                    trees.append((loc.lon, loc.lat))

    if trees:
        pts = np.array(trees, dtype=np.float64)
        scene.trees = proj.forward(pts[:, 0], pts[:, 1]).astype(np.float32)

    if verbose:
        print(f'  extracted in {time.time() - t0:.1f}s: {scene.stats()}')
    return scene


def _area(obj, scene, bbox, outer_bbox, proj):
    if _coarse_reject_area(obj, bbox):
        return
    tg = obj.tags
    building = T.is_building(tg)
    if not building:
        cls = T.surface_class(tg)
        if cls is None:
            return
    if T.is_underground(tg) and not building:
        return

    clip = outer_bbox if building else bbox
    try:
        rings = list(obj.outer_rings())
    except Exception:
        return

    for outer in rings:
        opts = _ring_points(outer)
        if len(opts) < 4 or not _hit(opts, clip):
            continue
        obb = _bbox_of(opts)
        if (obb[0] < clip[0] or obb[1] < clip[1] or
                obb[2] > clip[2] or obb[3] > clip[3]):
            if building:
                continue  # a building straddling the frame edge: just drop it
            opts = clip_polygon(opts, clip)
            if opts is None:
                continue
            holes = []
            for inner in obj.inner_rings(outer):
                ip = _ring_points(inner)
                if len(ip) >= 4 and _hit(ip, clip):
                    ip = clip_polygon(ip, clip)
                    if ip is not None:
                        holes.append(ip)
        else:
            holes = [_ring_points(i) for i in obj.inner_rings(outer)]
            holes = [h for h in holes if len(h) >= 4]

        outer_m = proj.forward(opts[:, 0], opts[:, 1]).astype(np.float32)
        holes_m = [proj.forward(h[:, 0], h[:, 1]).astype(np.float32) for h in holes]

        if building:
            oid = obj.orig_id()
            height, minh = T.building_height(tg, oid)
            wall, roof = T.building_colours(tg, oid)
            scene.buildings.append({
                'outer': outer_m, 'holes': holes_m,
                'height': height, 'min_height': minh,
                'wall': wall, 'roof': roof,
                'shape': T.roof_shape(tg, oid),
                'part': 'building' not in tg,
                'id': oid,
            })
        else:
            layer, rgb = T.SURFACES[cls]
            scene.surfaces.append({
                'outer': outer_m, 'holes': holes_m,
                'cls': cls, 'layer': layer, 'rgb': rgb,
            })


def _way(obj, scene, bbox, proj):
    tg = obj.tags
    highway = tg.get('highway')
    railway = tg.get('railway')
    waterway = tg.get('waterway')
    if (highway not in T.ROADS and railway not in T.RAILS
            and waterway not in T.WATERWAYS):
        return
    if _coarse_reject_way(obj, bbox) or T.is_underground(tg):
        return

    if highway in T.ROADS:
        width, layer, rgb = T.ROADS[highway]
        kind = 'road'
        if tg.get('area') == 'yes':
            return
        lanes = T.parse_length(tg.get('lanes'))
        if lanes and lanes > 2:
            width = max(width, lanes * 3.2)
    elif railway in T.RAILS:
        width, rgb = T.RAILS[railway]
        layer, kind = 4, 'rail'
    elif waterway in T.WATERWAYS:
        width = T.WATERWAYS[waterway]
        w = T.parse_length(tg.get('width'))
        if w:
            width = w
        layer, rgb, kind = 3, T.SURFACES['water'][1], 'water'
    else:
        return

    pts = _way_points(obj)
    if pts is None or len(pts) < 2 or not _hit(pts, bbox):
        return

    elev = T.structure_height(tg)
    for chain in clip_polyline(pts, bbox):
        if len(chain) < 2:
            continue
        scene.lines.append({
            'pts': proj.forward(chain[:, 0], chain[:, 1]).astype(np.float32),
            'width': width, 'layer': layer, 'rgb': rgb,
            'kind': kind, 'elev': elev,
        })


# --- caching ----------------------------------------------------------------

def cache_key(path, bbox):
    st = os.stat(path)
    raw = f'{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}|' \
          f'{bbox[0]:.6f},{bbox[1]:.6f},{bbox[2]:.6f},{bbox[3]:.6f}|{CACHE_VERSION}'
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def load(path, bbox, cache_dir='cache', use_cache=True, verbose=True):
    cache_file = None
    if use_cache and cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        cache_file = os.path.join(cache_dir, f'scene-{cache_key(path, bbox)}.pkl')
        if os.path.exists(cache_file):
            with open(cache_file, 'rb') as fh:
                scene = pickle.load(fh)
            if verbose:
                print(f'  loaded from cache: {scene.stats()}')
            return scene

    scene = extract(path, bbox, verbose=verbose)
    if cache_file:
        with open(cache_file, 'wb') as fh:
            pickle.dump(scene, fh, protocol=pickle.HIGHEST_PROTOCOL)
    return scene
