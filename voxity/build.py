"""Turn extracted OSM features into GPU-ready triangle soup.

The vertex layout and `MeshBuilder` live in mesh.py, shared with voxel.py so
extruded footprints and voxel models land in the same buffer. Only matte and
water come out of here; foliage never reaches this buffer at all — trees are
instanced through their own program in renderer.py.
"""

import numpy as np
from mapbox_earcut import triangulate_float32

from . import place
from .geo import dedup_ring, oriented_box, signed_area
from .mesh import MAT_MATTE, MAT_WATER, MeshBuilder, orient_triangles


LAYER_STEP = 0.15      # vertical spacing between flat ground layers
CASING_DROP = 0.06
GROUND_COLOUR = (0.30, 0.31, 0.28)
SURROUND_COLOUR = (0.27, 0.29, 0.26)
UP = np.array([0.0, 1.0, 0.0], dtype=np.float32)


def _triangulate(outer, holes):
    """Fan out a polygon with holes; returns an (N,2) array of triangle corners."""
    outer = dedup_ring(outer)
    if len(outer) < 3:
        return None
    rings = [outer]
    ends = [len(outer)]
    for h in holes:
        h = dedup_ring(h)
        if len(h) >= 3:
            rings.append(h)
            ends.append(ends[-1] + len(h))
    verts = np.ascontiguousarray(np.vstack(rings), dtype=np.float32)
    try:
        idx = triangulate_float32(verts, np.array(ends, dtype=np.uint32))
    except Exception:
        return None
    if len(idx) == 0:
        return None
    return verts[idx]


def _jitter(seed, amount=0.06):
    """Deterministic per-feature brightness wobble so blocks aren't uniform."""
    h = (seed * 2654435761) & 0xFFFFFFFF
    return 1.0 + ((h >> 8 & 0xFFFF) / 65535.0 - 0.5) * 2.0 * amount


def _flat(mb, tri2d, y, rgb, mat=MAT_MATTE):
    pos = np.empty((len(tri2d), 3), dtype=np.float32)
    pos[:, 0] = tri2d[:, 0]
    pos[:, 1] = y
    pos[:, 2] = tri2d[:, 1]
    mb.add(pos, UP, rgb, mat)


# --- ribbons ----------------------------------------------------------------

def ribbon(pts, width):
    """Expand a polyline into a quad strip with mitred joins.

    Returns (left, right) arrays of shape (N,2).
    """
    pts = np.asarray(pts, dtype=np.float64)
    d = np.diff(pts, axis=0)
    seg_len = np.linalg.norm(d, axis=1)
    good = seg_len > 1e-6
    if not good.any():
        return None
    if not good.all():
        pts = np.vstack([pts[:1], pts[1:][good]])
        d = np.diff(pts, axis=0)
        seg_len = np.linalg.norm(d, axis=1)
        if len(d) == 0:
            return None
    d /= seg_len[:, None]

    # per-segment left normal, then average into per-vertex miters
    nrm = np.stack([-d[:, 1], d[:, 0]], axis=1)
    prev = np.vstack([nrm[:1], nrm])
    nxt = np.vstack([nrm, nrm[-1:]])
    miter = prev + nxt
    ml = np.linalg.norm(miter, axis=1)
    ml[ml < 1e-9] = 1.0
    miter /= ml[:, None]
    scale = 1.0 / np.clip((miter * nxt).sum(axis=1), 0.35, 1.0)
    off = miter * (scale * width * 0.5)[:, None]
    return pts + off, pts - off


def _add_ribbon(mb, pts, width, y, rgb, mat=MAT_MATTE):
    r = ribbon(pts, width)
    if r is None:
        return
    left, right = r
    n = len(left) - 1
    if n < 1:
        return
    quads = np.empty((n, 6, 2), dtype=np.float64)
    quads[:, 0] = left[:-1]
    quads[:, 1] = right[:-1]
    quads[:, 2] = right[1:]
    quads[:, 3] = left[:-1]
    quads[:, 4] = right[1:]
    quads[:, 5] = left[1:]
    flat = quads.reshape(-1, 2)
    pos = np.empty((len(flat), 3), dtype=np.float32)
    pos[:, 0] = flat[:, 0]
    pos[:, 1] = y
    pos[:, 2] = flat[:, 1]
    mb.add(pos, UP, rgb, mat)


# --- buildings --------------------------------------------------------------

def _gable_roof(mb, box, top, height, colour):
    """Two slanted planes plus triangular gable ends, on the long axis."""
    (lo, hi, u, v) = box
    if hi[0] - lo[0] < hi[1] - lo[1]:         # ridge runs along the long side
        u, v = v, -u
        lo, hi = (np.array([lo[1], -hi[0]]), np.array([hi[1], -lo[0]]))

    def pt(a, b, y):
        w = u * a + v * b
        return (float(w[0]), float(y), float(w[1]))

    x0, x1 = lo[0], hi[0]
    z0, z1 = lo[1], hi[1]
    zm = 0.5 * (z0 + z1)
    ridge_top = top + height

    a = pt(x0, z0, top)
    b = pt(x1, z0, top)
    c = pt(x1, z1, top)
    d = pt(x0, z1, top)
    r0 = pt(x0, zm, ridge_top)
    r1 = pt(x1, zm, ridge_top)

    # cross-section normals: the pitch rises by `height` over half the width
    half = 0.5 * (z1 - z0)
    near = np.array([-height * v[0], half, -height * v[1]], dtype=np.float32)
    far = np.array([height * v[0], half, height * v[1]], dtype=np.float32)
    near /= max(np.linalg.norm(near), 1e-6)
    far /= max(np.linalg.norm(far), 1e-6)

    for tri, n in (((a, b, r1), near), ((a, r1, r0), near),
                   ((d, r0, r1), far), ((d, r1, c), far)):
        mb.add(np.array(tri, dtype=np.float32), n, colour * 1.06, MAT_MATTE)

    gable = colour * 0.9
    for tri, sgn in (((a, r0, d), -1.0), ((b, c, r1), 1.0)):
        pos = np.array(tri, dtype=np.float32)
        n = np.array([u[0] * sgn, 0.0, u[1] * sgn], dtype=np.float32)
        mb.add(pos, n, gable, MAT_MATTE)


PART_INSET = 0.25          # metres a building:part is pulled in from its parent


def _inset(ring, amount):
    """Pull a ring towards its centroid, to break coplanar surfaces apart."""
    c = ring.mean(axis=0)
    d = ring - c
    r = np.linalg.norm(d, axis=1)
    scale = np.clip(1.0 - amount / np.maximum(r, 1e-3), 0.4, 1.0)
    return (c + d * scale[:, None]).astype(np.float32)


def _add_building(mb, b):
    outer = b['outer']
    top, base = b['height'], b['min_height']
    if b.get('part'):
        # building:part shares its parent's footprint exactly; nudging it in
        # (and up) stops the two sets of walls and roofs from z-fighting
        outer = _inset(np.asarray(outer, dtype=np.float64), PART_INSET)
        top += 0.06
    tint = _jitter(b['id'] & 0xFFFFFF)
    wall = np.clip(np.array(b['wall'], dtype=np.float32) * tint, 0, 1)
    roof = np.clip(np.array(b['roof'], dtype=np.float32) * tint, 0, 1)

    cap = _triangulate(outer, b['holes'])
    if cap is None:
        return
    _flat(mb, cap, top, roof)

    if b.get('shape') == 'gabled' and not b['holes'] and top - base < 26.0:
        ring = dedup_ring(np.asarray(outer, dtype=np.float64))
        if 3 <= len(ring) <= 10:
            fit = oriented_box(ring)
            if fit is not None:
                box, fill = fit
                size = box[1] - box[0]
                short = min(size)
                if fill > 0.90 and 3.0 < short < 26.0 and max(size) < 70.0:
                    _gable_roof(mb, box, top, min(short * 0.35, 4.5), roof)

    for ring, is_hole in [(outer, False)] + [(h, True) for h in b['holes']]:
        ring = dedup_ring(np.asarray(ring, dtype=np.float64))
        if len(ring) < 3:
            continue
        area = signed_area(ring)
        if abs(area) < 1e-3:
            continue
        # Normalise the winding (outer rings anticlockwise, holes clockwise)
        # so the outward normal of edge e is always (e.z, 0, -e.x). OSM does
        # not guarantee a consistent direction and a flipped ring turns the
        # whole building inside out under back-face culling.
        if (area > 0) == is_hole:
            ring = ring[::-1]

        p = ring
        q = np.roll(ring, -1, axis=0)
        e = q - p
        el = np.linalg.norm(e, axis=1)
        keep = el > 1e-6
        p, q, e, el = p[keep], q[keep], e[keep], el[keep]
        if len(p) == 0:
            continue
        e = e / el[:, None]

        m = len(p)
        v = np.empty((m, 6, 3), dtype=np.float32)
        for slot, (src, h) in enumerate(((p, base), (q, base), (q, top),
                                         (p, base), (q, top), (p, top))):
            v[:, slot, 0] = src[:, 0]
            v[:, slot, 1] = h
            v[:, slot, 2] = src[:, 1]

        nrm = np.zeros((m, 3), dtype=np.float32)
        nrm[:, 0] = e[:, 1]
        nrm[:, 2] = -e[:, 0]
        nrm = np.repeat(nrm, 6, axis=0)

        # fake contact shading: darker where the wall meets the ground
        shade = np.array([0.62, 0.62, 1.0, 0.62, 1.0, 1.0], dtype=np.float32)
        col = (wall[None, None, :] * shade[None, :, None]).repeat(m, axis=0)
        mb.add(v.reshape(-1, 3), nrm, col.reshape(-1, 3), MAT_MATTE)


# --- trees ------------------------------------------------------------------

def tree_mesh(segments=7):
    """Unit tree (trunk + two canopy cones), 1 m tall, 1 m wide."""
    pos, nrm = [], []
    ang = np.linspace(0, 2 * np.pi, segments, endpoint=False)
    cs, sn = np.cos(ang), np.sin(ang)

    def cone(y0, y1, r0, r1):
        for i in range(segments):
            j = (i + 1) % segments
            a = (r0 * cs[i], y0, r0 * sn[i])
            b = (r0 * cs[j], y0, r0 * sn[j])
            c = (r1 * cs[j], y1, r1 * sn[j])
            d = (r1 * cs[i], y1, r1 * sn[i])
            for tri in ((a, b, c), (a, c, d)):
                for v in tri:
                    pos.append(v)
                    n = np.array([v[0], (r0 - r1) * 0.5, v[2]], dtype=np.float32)
                    ln = np.linalg.norm(n)
                    nrm.append(n / ln if ln > 1e-6 else UP)

    cone(0.0, 0.36, 0.05, 0.04)       # trunk
    cone(0.30, 0.52, 0.13, 0.30)      # canopy flares out
    cone(0.52, 0.80, 0.30, 0.24)      # broadest part
    cone(0.80, 1.0, 0.24, 0.02)       # crown
    trunk_verts = segments * 6
    p = np.array(pos, dtype=np.float32)
    n = np.array(nrm, dtype=np.float32)
    orient_triangles(p, n)
    return p, n, trunk_verts


def tree_instances(points, rng):
    """Per-instance (x, z, height, radius, tint) for each tree node."""
    n = len(points)
    if n == 0:
        return np.zeros((0, 5), dtype=np.float32)
    inst = np.empty((n, 5), dtype=np.float32)
    inst[:, 0] = points[:, 0]
    inst[:, 1] = points[:, 1]
    inst[:, 2] = rng.uniform(7.0, 15.0, n)
    inst[:, 3] = inst[:, 2] * rng.uniform(0.55, 0.85, n)
    inst[:, 4] = rng.uniform(0.78, 1.18, n)
    return inst


# --- top level --------------------------------------------------------------

def build_scene(scene, seed=7, verbose=True, voxel_houses=True):
    rng = np.random.default_rng(seed)
    mb = MeshBuilder()

    # Buildings whose footprint is one of the surveyed plans get a voxel house
    # instead of an extrusion. Decided up front so `_add_building` keeps meaning
    # exactly what it always did — it is the fallback, not a special case.
    lib = place.load() if voxel_houses else None
    houses, scene_buildings = place.place_all(scene.buildings, lib, verbose)

    minx, minz, maxx, maxz = scene.extent

    def quad(x0, z0, x1, z1):
        return np.array([[x0, z0], [x1, z0], [x1, z1],
                         [x0, z0], [x1, z1], [x0, z1]], dtype=np.float32)

    # a skirt of bare land so the extract doesn't read as a floating island
    pad = max(maxx - minx, maxz - minz) * 2.0 + 2000.0
    _flat(mb, quad(minx - pad, minz - pad, maxx + pad, maxz + pad), -0.08,
          SURROUND_COLOUR)
    _flat(mb, quad(minx, minz, maxx, maxz), 0.0, GROUND_COLOUR)

    for s in sorted(scene.surfaces, key=lambda s: s['layer']):
        tri = _triangulate(s['outer'], s['holes'])
        if tri is None:
            continue
        water = s['cls'] == 'water'
        rgb = np.array(s['rgb'], dtype=np.float32) * _jitter(len(tri) * 7 + s['layer'], 0.04)
        _flat(mb, tri, s['layer'] * LAYER_STEP, np.clip(rgb, 0, 1),
              1.0 if water else 0.0)

    for ln in sorted(scene.lines, key=lambda l: (l['elev'], l['layer'])):
        y = ln['layer'] * LAYER_STEP + ln['elev']
        rgb = np.array(ln['rgb'], dtype=np.float32)
        if ln['kind'] == 'water':
            _add_ribbon(mb, ln['pts'], ln['width'], y, rgb, MAT_WATER)
            continue
        if ln['width'] >= 4.0:
            _add_ribbon(mb, ln['pts'], ln['width'] + 1.4, y - CASING_DROP, rgb * 0.6)
        _add_ribbon(mb, ln['pts'], ln['width'], y, rgb)
        if ln['kind'] == 'rail':
            _add_ribbon(mb, ln['pts'], ln['width'] * 0.25, y + 0.03, rgb * 1.9)

    for b in scene_buildings:
        _add_building(mb, b)

    # houses arrive already packed in the shared layout, so they only have to be
    # concatenated; order does not matter, this is opaque depth-tested geometry
    verts = mb.pack()
    if houses:
        verts = np.vstack([verts, *houses]).astype(np.float32)
    trees = tree_instances(scene.trees, rng)
    if verbose:
        print(f'  meshed {len(verts) // 3} triangles, {len(trees)} trees')
    return verts, trees
