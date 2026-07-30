"""Stand a voxel house on every OSM building whose footprint matches one.

The last link of the shapes pipeline, and the only one that runs at play time.
`--build-footprints` finds the shapes a city repeats, `--build-houses` (and then
you, in the editor) puts a house on each; this looks at a real building, decides
which of those plans it *is*, and drops one of that plan's houses onto it. A
building matching nothing is extruded exactly as it always was, so the city
degrades to what it was before rather than to holes in the ground.

Matching reuses the survey wholesale — `footprints.footprint_mask` for the
building, `footprints.align` for the fit — which is the point of the two having
been written to share a rasteriser. The one thing this needs and the survey does
not is where the mask *came from*: `footprint_mask` hands back the oriented
frame it rotated into, and `align` says which of the eight dihedral transforms
won, so together they say exactly where in the world each house voxel goes.

Three things make it cheap enough to run over a whole square:

* Houses are meshed **once per (house, transform)**, not once per building. What
  differs between two buildings of the same shape is a translation and a
  rotation, and both are arguments to `voxel.mesh_vertices`.
* Candidate plans are pruned on bounding box and filled-cell count before
  `align` runs, on exactly the grounds `footprints.families` prunes on: neither
  can discard a fit that would have passed the threshold.
* The whole thing is behind the mesh cache, so a square pays for it once. That
  is also why the pick is a hash of the OSM id and never `random` — a second run
  has to agree with the cache the first one wrote.

Placed houses carry `MAT_VOXEL`, so the city lights them with its own sun and
they show the same per-cell mosaic as the editor. One caveat, and it is visible
if you look for it: that mosaic is a hash of the **world** cell (see
`shaders.voxel_value_glsl`), so on a building that does not run north-south the
lattice no longer lines up with the house's own voxels. It still reads as voxel
noise, because it is; it is simply not the same noise the editor drew. Making it
follow the rotation would mean handing the shader a per-building frame, and the
vertex layout has no room for one. The alternative — baking the value into the
vertex colour — costs the greedy mesher entirely, since neighbouring cells hash
differently and nothing would merge: a few hundred triangles a house becomes a
few thousand.
"""

import hashlib
import os
import time
from collections import defaultdict

import numpy as np

from . import footprints as F
from . import voxel

HOUSE_DIR = 'models/houses'

# Overlap at which a building counts as being "this plan". Higher than the
# survey's IOU_JOIN on purpose: joining two shapes into a family only has to
# decide they are the same *kind* of shape, while this decides that a specific
# house may stand where a specific building is, and a wall a metre out is a
# wall a metre out.
MATCH_IOU = 0.90
# Slack when fitting a plan over a building, in cells. Derived from metres, so
# it means the same thing whatever `voxel.CELL_M` is set to — as a constant
# number of cells it silently became four times stricter when the grid did.
MARGIN = F.match_margin()

# How far a house may be from the building's real height before it is better to
# extrude. Without this every tower block gets a five-storey house on it.
HEIGHT_TOL_M = 3.0
HEIGHT_TOL_REL = 0.30


def _pick(seed, n):
    """A stable index in [0, n) — the same building always gets the same house.

    Deterministic like everything else in build.py, and for the sharper reason
    that the mesh is cached: a second run picking differently would contradict
    the .npz the first one wrote (see MESH_VERSION in main.py).
    """
    h = ((seed & 0xFFFFFFFF) * 2654435761) & 0xFFFFFFFF
    return ((h >> 9) ^ (h & 0x1FF)) % n


def cells_mask(cells):
    """A {(x, z)} footprint as (mask, x0, z0), the mask cropped to its cells."""
    xs = [c[0] for c in cells]
    zs = [c[1] for c in cells]
    x0, z0 = min(xs), min(zs)
    mask = np.zeros((max(zs) - z0 + 1, max(xs) - x0 + 1), dtype=bool)
    for x, z in cells:
        mask[z - z0, x - x0] = True
    return mask, x0, z0


class Plan:
    """One footprint, the houses standing on it, and their meshes.

    `quads` is memoised per (house, transform) because that is the unit of work
    that repeats: a hundred buildings of the same shape are a hundred different
    placements of at most eight meshes.
    """

    def __init__(self, cells, models, cell):
        self.mask, self.x0, self.z0 = cells_mask(cells)
        self.margin = F.match_margin(cell)
        self.filled = int(np.count_nonzero(self.mask))
        self.cell = cell
        self.houses = [{'cells': _packed(v), 'height_m': _height(v) * cell,
                        'name': n} for n, v in models]
        self.houses.sort(key=lambda h: h['height_m'])
        self._quads = {}
        # A voxel off the plan has nowhere to go — `quads` has only the plan's
        # cells to map through, and placing it anyway would push geometry into
        # whatever is next door. Dropping it is right; doing so silently is not.
        self.strays = {h['name']: n for h in self.houses
                       if (n := int((~self._on_plan(h['cells'])).sum()))}

    def _on_plan(self, arr):
        """Which rows of a packed house sit over the plan's own cells."""
        pz, px = arr[:, 2] - self.z0, arr[:, 0] - self.x0
        h, w = self.mask.shape
        return (pz >= 0) & (pz < h) & (px >= 0) & (px < w)

    def pick(self, target_m, seed):
        """A house near `target_m` tall, or None when none of them is close.

        Returning None rather than the least-bad house is what keeps a tower
        block a tower block: the plans fit its floor, but none of these houses
        does, and an extrusion is a better answer than a bungalow.
        """
        if not self.houses:
            return None
        tol = max(HEIGHT_TOL_M, HEIGHT_TOL_REL * target_m)
        near = [i for i, h in enumerate(self.houses)
                if abs(h['height_m'] - target_m) <= tol]
        if not near:
            return None
        return near[_pick(seed, len(near))]

    def quads(self, house, vi):
        """Greedy quads for house `house` under dihedral transform `vi`.

        The transform is applied to the *voxels* and the result meshed, rather
        than applied to the mesh: a mirror flips winding, and re-meshing a
        mirrored model is both simpler and free, since it happens eight times at
        most however many buildings ask for it.
        """
        key = (house, vi)
        got = self._quads.get(key)
        if got is None:
            arr = self.houses[house]['cells']
            arr = arr[self._on_plan(arr)]
            vmap = F.variant_cells(self.mask.shape, vi)
            tz, tx = vmap[arr[:, 2] - self.z0, arr[:, 0] - self.x0].T
            # the dict `build_mesh` wants is built here and dropped again: at a
            # quarter-metre cell a house is tens of thousands of voxels, and one
            # per model kept resident is the difference between a library that
            # fits in a few tens of megabytes and one that does not
            moved = {(int(a), int(b), int(c)): int(d)
                     for a, b, c, d in zip(tx, arr[:, 1], tz, arr[:, 3],
                                           strict=True)}
            got = voxel.build_mesh(moved)
            self._quads[key] = got
        return got


def _packed(voxels):
    """A voxel dict as an (N, 4) int32 array of (x, y, z, hue).

    Houses are kept in this form rather than as dicts because the library holds
    every one of them at once: a dict of 24,000 cell tuples is a couple of
    megabytes, and a hundred and sixty of those is a third of a gigabyte.
    """
    if not voxels:
        return np.zeros((0, 4), dtype=np.int32)
    return np.array([(x, y, z, h) for (x, y, z), h in voxels.items()],
                    dtype=np.int32)


def _height(voxels):
    b = voxel.bounds(voxels)
    return 0 if b is None else b[1][1]


class Library:
    """Every plan on disk, with the pruning index the match needs."""

    def __init__(self, plans, cell):
        self.plans = plans
        self.cell = cell
        self.margin = F.match_margin(cell)
        self.buckets = defaultdict(list)
        for i, p in enumerate(plans):
            self.buckets[tuple(sorted(p.mask.shape))].append(i)
        self.tried = self.placed = 0

    def __len__(self):
        return len(self.plans)

    def candidates(self, mask):
        """Plans that could still reach MATCH_IOU — both prunes are sound.

        A bounding box more than `self.margin` out cannot be fitted at all, and
        the overlap of two masks never exceeds the smaller filled count over the
        larger, neither can a plan outside that band.
        """
        h, w = mask.shape
        n = int(np.count_nonzero(mask))
        near = set()
        m = self.margin
        for dh in range(-m, m + 1):
            for dw in range(-m, m + 1):
                near.update(self.buckets.get(tuple(sorted((h + dh, w + dw))), ()))
        return [i for i in near
                if min(n, self.plans[i].filled)
                >= MATCH_IOU * max(n, self.plans[i].filled)]

    def match(self, mask):
        """Best (plan, transform, offset, iou) over `mask`, or None."""
        best_score, best = MATCH_IOU, None
        for i in self.candidates(mask):
            plan = self.plans[i]
            score, vi, off = F.align(plan.mask, mask, self.margin,
                                     floor=best_score)
            if vi is not None and score > best_score:
                best_score, best = score, (plan, vi, off, score)
        return best

    def place(self, b):
        """Vertices for a house standing on building `b`, or None to extrude it.

        `b` is a Scene building: `outer`/`holes` already projected to metres,
        `height` and `min_height` in metres, `id` the OSM id.
        """
        self.tried += 1
        got = F.footprint_mask(b['outer'], b['holes'], self.cell)
        if got is None:
            return None
        mask, _, _, frame = got
        hit = self.match(mask)
        if hit is None:
            return None
        plan, vi, off, _ = hit
        base = b['min_height']
        house = plan.pick(max(b['height'] - base, 0.0), b['id'])
        if house is None:
            return None

        # A mask cell (z, x) covers box coordinates ((x + col0) * cell,
        # (z + row0) * cell), and `off` says where the transformed plan sits in
        # the building's mask (padded by the margin). Fold both into one translation
        # in cells, so the mesh itself only ever needs the rotation.
        lo, u, v, (row0, col0) = frame
        cx = off[1] - self.margin + col0
        cz = off[0] - self.margin + row0
        a = cx * self.cell + lo[0]
        c = cz * self.cell + lo[1]
        basis = np.array([[u[0], 0.0, v[0]],
                          [0.0, 1.0, 0.0],
                          [u[1], 0.0, v[1]]], dtype=np.float64)
        offset = (a * u[0] + c * v[0], base, a * u[1] + c * v[1])
        self.placed += 1
        return voxel.mesh_vertices(plan.quads(house, vi), self.cell, offset,
                                   basis=basis)


def load(house_dir=HOUSE_DIR, cell=F.CELL, verbose=True):
    """Group every model in `house_dir` by the footprint it records.

    The footprint is read out of the file rather than parsed from its name: a
    house is defined by the ground it was built on, and that survives being
    renamed, hand-edited or drawn from scratch. A model with no footprint is
    skipped — there is nothing to match it against.
    """
    try:
        names = sorted(f for f in os.listdir(house_dir) if f.endswith('.json'))
    except OSError:
        return None
    by_plan = defaultdict(list)
    for name in names:
        path = os.path.join(house_dir, name)
        try:
            cells = voxel.load_footprint(path)
            vox = voxel.load(path)
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if cells and vox:
            by_plan[tuple(sorted(cells))].append((name, vox))
    plans = [Plan(cells, models, cell) for cells, models in by_plan.items()]
    if verbose:
        print(f'  {sum(len(p.houses) for p in plans)} houses on '
              f'{len(plans)} footprints from {house_dir}/')
        stray = {n: c for p in plans for n, c in p.strays.items()}
        if stray:
            worst = sorted(stray.items(), key=lambda kv: -kv[1])[:3]
            print('  note: ' + ', '.join(f'{n} ({c} voxels)' for n, c in worst)
                  + (' and others' if len(stray) > 3 else '')
                  + ' reach past their footprint; those voxels are not placed')
    return Library(plans, cell) if plans else None


def signature(house_dir=HOUSE_DIR):
    """A digest of the house directory, for folding into a mesh cache key.

    Editing a house has to invalidate the city meshes built with it, and no
    version constant would notice — the models are data, not code.
    """
    try:
        names = sorted(os.listdir(house_dir))
    except OSError:
        return 'none'
    h = hashlib.sha1()
    for name in names:
        st = os.stat(os.path.join(house_dir, name))
        h.update(f'{name}|{st.st_size}|{int(st.st_mtime)}|'.encode())
    return h.hexdigest()[:12]


def place_all(buildings, lib, verbose=True):
    """Split `buildings` into placed houses and the ones still to be extruded.

    Returns (list of vertex arrays, list of buildings that got nothing), so the
    caller never has to ask twice whether a building was handled.
    """
    if lib is None:
        return [], list(buildings)
    t0 = time.time()
    out, rest = [], []
    for b in buildings:
        # a building:part sits on its parent's plan and would double the walls
        verts = None if b.get('part') else lib.place(b)
        if verts is not None and len(verts):
            out.append(verts)
        else:
            rest.append(b)
    if verbose:
        tris = sum(len(v) for v in out) // 3
        print(f'  placed {len(out)} of {lib.tried} footprints as voxel houses '
              f'({tris:,} triangles) in {time.time() - t0:.1f}s')
    return out, rest
