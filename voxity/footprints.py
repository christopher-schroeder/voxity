"""The footprint shapes a city repeats, as voxel-grid masks.

The city extrudes whatever footprint it finds in the extract; the editor builds
models out of voxels. This is the bridge: it streams the whole `.osm.pbf`,
reduces every building's footprint to a mask on the **voxel** grid, and groups
those masks so the handful of shapes a city actually repeats falls out. Each
group is written as a one-layer voxel model, so `--editor --model <file>` opens a
real footprint ready to be built up into a house by hand.

**Shape and size are separated on purpose, and that is the whole design.** Group
on the voxel masks directly and the answer is two dozen rectangles differing only
in size, because that is genuinely what a city is most made of — true, and no use
to somebody who has to model each one. So grouping happens twice:

* A **family** is a shape normalised to a fixed resolution (`NORM_LONG` cells on
  its long axis), which keeps its aspect ratio and its corners but throws its
  size away. Every 2:1 rectangle is one family whatever its metres; an L is a
  different family; so is a squarer rectangle.
* A family's **sizes** are the concrete cell dimensions its members really had,
  and each family contributes its commonest few as separate models. Size cannot
  be normalised away in the output: `Renderer.voxel_cell` is one uniform for the
  whole scene, so a voxel model can never be rescaled when it is placed. Giving
  size its own axis is what stops it competing with shape for output slots.

Rotation is removed before either step by rotating each footprint onto its own
minimum-area rectangle (`geo.oriented_box`), and quarter turns and mirroring are
canonicalised away, since placing a model turned or mirrored costs nothing —
voxel models are meshed on load, never cached.

Like overview.py this streams rather than building a `Scene`: 355k buildings as
Python dicts is the thing to avoid, while a mask is a few dozen bytes. No GL and
no pygame at import time, so it runs headless and costs nothing to import.
"""

import hashlib
import json
import os
import pickle
import time
from collections import Counter, defaultdict

import numpy as np
import osmium

from . import tags as T
from . import voxel
from .geo import Projection, dedup_ring, oriented_box

FOOTPRINT_VERSION = 3

# Only objects that could be a building; much narrower than extract.KEYS.
KEYS = ('building',)

# Cell size in metres. 1.0 matches `Renderer.voxel_cell`, so a model built on
# one of these footprints drops into a city without re-meshing.
CELL = 1.0

# Sub-samples per cell per axis when rasterising. A cell is filled when the
# footprint covers at least half of it, so 2 means 2 of the 4 sub-cells.
SUPERSAMPLE = 2

# What is worth treating as a repeatable shape at all. The area bounds drop
# sheds and shopping centres; the cell and edge caps keep the rasteriser's
# working set small and would not admit a "basic shape" anyway.
MIN_AREA_M2 = 20.0
MAX_AREA_M2 = 4000.0
MAX_CELLS = 64
MAX_EDGES = 64

# Slack given to `geo.oriented_box` when it picks the frame to rotate into.
# Without it an octagon's frame flips between its axis-aligned and its 45-degree
# fit as the building turns, and one shape lands in a dozen families.
FRAME_TOL = 0.02

# Cells on the long axis of a *normalised* shape: the resolution at which two
# footprints are compared as shapes. It has to be fine enough to resolve the
# thinnest feature worth keeping — a T with a stem 1/5 of its width normalises
# to a stem 2.4 cells wide at 12, which thresholds to 2 or 3 depending on where
# the grid happens to fall, and one T then lands in three families. At 20 that
# stem is 4 cells wide at every size, so nothing flickers and `IOU_JOIN` can
# stay high enough to keep an octagon apart from a square.
NORM_LONG = 20

# Overlap (intersection over union) at which two normalised shapes are the same
# family, and cells of slack allowed when fitting one over the other.
IOU_JOIN = 0.88
ALIGN_MARGIN = 1

# Shapes put through family grouping. Grouping is quadratic in candidates, and a
# city's tail is overwhelmingly shapes seen exactly once, which can neither form
# a top family nor change the order of one — so cut on how often a shape occurs,
# with a hard cap as a backstop. `families` reports the coverage this costs.
MIN_SHAPE_COUNT = 5
MAX_CANDIDATES = 20_000

FOOTPRINT_HUE = 20            # what the written footprint layer is coloured

# Where --build-footprints writes, and where the editor looks for grounds.
OUT_DIR = 'models/footprints'


# --- rasterising one footprint ---------------------------------------------

def _edges(rings):
    """(E, 4) array of (x0, z0, x1, z1) for a list of rings."""
    return np.vstack([np.hstack([r, np.roll(r, -1, axis=0)]) for r in rings])


def _rasterise(edges, nx, nz, cell, ss=SUPERSAMPLE):
    """Even-odd scanline fill of `edges` onto an nx by nz grid of `cell` metres.

    Holes come in as more edges rather than as a special case: even-odd parity
    punches a courtyard out on its own, which is the whole reason to use it.

    Parity is accumulated as a running count along x rather than by testing
    every sample against every edge — that tensor is (rows x cols x edges) and
    made this pass three times slower than the osmium read feeding it.
    """
    step = cell / ss
    nrow, ncol = nz * ss, nx * ss
    zs = (np.arange(nrow) + 0.5) * step
    x0, z0, x1, z1 = edges.T
    dz = z1 - z0
    # half-open in z, so a vertex shared by two edges is crossed exactly once
    lo, hi = np.minimum(z0, z1), np.maximum(z0, z1)
    hit = (lo[None, :] <= zs[:, None]) & (zs[:, None] < hi[None, :])
    row, col = np.nonzero(hit)
    if not len(row):
        return np.zeros((nz, nx), dtype=bool)

    with np.errstate(divide='ignore', invalid='ignore'):
        t = (zs[row] - z0[col]) / dz[col]
    xint = x0[col] + t * (x1 - x0)[col]
    # first sample index at or right of the crossing: samples sit at (i+0.5)
    first = np.ceil(xint / step - 0.5).astype(np.int64)
    np.clip(first, 0, ncol, out=first)

    acc = np.zeros((nrow, ncol + 1), dtype=np.int32)
    np.add.at(acc, (row, first), 1)
    sub = np.cumsum(acc[:, :ncol], axis=1) & 1
    return sub.reshape(nz, ss, nx, ss).sum(axis=(1, 3)) * 2 >= ss * ss


def _trim(mask):
    """Crop to the occupied bounding box, or None when nothing is set."""
    rows = np.flatnonzero(mask.any(axis=1))
    cols = np.flatnonzero(mask.any(axis=0))
    if not len(rows) or not len(cols):
        return None
    return mask[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]


def footprint_mask(outer, holes, cell=CELL):
    """One footprint as (mask, area_m2, span_m), or None if it is no candidate.

    The ring is rotated onto its own walls first, so `mask` rows run along the
    oriented box's short axis and columns along its long one — the orientation a
    model would be built in, not any compass direction. `span` is that box in
    metres, unrounded, which is what `normalise` needs to be scale-free.
    """
    ring = dedup_ring(np.asarray(outer, dtype=np.float64))
    if len(ring) < 3 or len(ring) > MAX_EDGES:
        return None
    fit = oriented_box(ring, tol=FRAME_TOL)
    if fit is None:
        return None
    (lo, hi, u, v), _ = fit
    span = hi - lo
    if span[0] * span[1] < MIN_AREA_M2 or span[0] * span[1] > MAX_AREA_M2 * 2:
        return None
    nx = max(1, round(span[0] / cell))
    nz = max(1, round(span[1] / cell))
    if nx > MAX_CELLS or nz > MAX_CELLS:
        return None

    rings = [np.stack([ring @ u - lo[0], ring @ v - lo[1]], axis=1)]
    n_edges = len(ring)
    for h in holes:
        hr = dedup_ring(np.asarray(h, dtype=np.float64))
        if len(hr) >= 3 and n_edges + len(hr) <= MAX_EDGES:
            rings.append(np.stack([hr @ u - lo[0], hr @ v - lo[1]], axis=1))
            n_edges += len(hr)

    mask = _trim(_rasterise(_edges(rings), nx, nz, cell))
    if mask is None:
        return None
    area = float(np.count_nonzero(mask)) * cell * cell
    if area < MIN_AREA_M2 or area > MAX_AREA_M2:
        return None
    return mask, area, (float(span[0]), float(span[1]))


# --- normalising away size -------------------------------------------------

def _integral(mask):
    integ = np.zeros((mask.shape[0] + 1, mask.shape[1] + 1), dtype=np.int64)
    integ[1:, 1:] = np.cumsum(np.cumsum(mask, axis=0), axis=1)
    return integ


def _integral_at(integ, zs, xs):
    """Bilinear read of an integral image at fractional coordinates.

    Exact rather than approximate: the true area integral of a per-cell constant
    image is bilinear inside every cell, so this returns covered area, not a
    guess at it.
    """
    z0 = np.clip(np.floor(zs).astype(int), 0, integ.shape[0] - 1)
    x0 = np.clip(np.floor(xs).astype(int), 0, integ.shape[1] - 1)
    z1 = np.minimum(z0 + 1, integ.shape[0] - 1)
    x1 = np.minimum(x0 + 1, integ.shape[1] - 1)
    fz = (zs - z0)[:, None]
    fx = (xs - x0)[None, :]
    top = integ[np.ix_(z0, x0)] * (1 - fx) + integ[np.ix_(z0, x1)] * fx
    bot = integ[np.ix_(z1, x0)] * (1 - fx) + integ[np.ix_(z1, x1)] * fx
    return top * (1 - fz) + bot * fz


def normalise(mask, span=None, long_cells=NORM_LONG):
    """Resample a mask so its long axis is `long_cells`, keeping aspect ratio.

    Size is thrown away and shape is kept: a 2:1 rectangle comes out the same
    whether it was 8 x 4 m or 30 x 15 m. Cells are area-averaged and
    re-thresholded at half coverage, so a thin wing survives shrinking rather
    than falling through the sampling grid.

    `span` is the footprint's true (width, height) in metres, and passing it
    matters: taken from the mask's own cell dimensions the ratio is already
    rounded, and one real shape measured at two sizes then lands on different
    twelfths — a 16 x 20 m L rasterises to 13 x 16 cells at one scale and
    22 x 28 at another, and 13/16 and 22/28 do not round alike.
    """
    h, w = mask.shape
    if h <= 0 or w <= 0:
        return None
    ratio_w, ratio_h = (w, h) if span is None else (float(span[0]),
                                                    float(span[1]))
    if ratio_w >= ratio_h:
        nw = long_cells
        nh = max(1, round(ratio_h / ratio_w * long_cells))
    else:
        nh = long_cells
        nw = max(1, round(ratio_w / ratio_h * long_cells))
    integ = _integral(mask)
    zs = np.linspace(0, h, nh + 1)
    xs = np.linspace(0, w, nw + 1)
    s = _integral_at(integ, zs, xs)
    covered = s[1:, 1:] - s[:-1, 1:] - s[1:, :-1] + s[:-1, :-1]
    out = covered >= 0.5 * (h / nh) * (w / nw)
    return _trim(out)


# --- canonical form --------------------------------------------------------

def variants(mask):
    """The 8 dihedral transforms of a mask: 4 quarter turns, each mirrored."""
    out = []
    m = mask
    for _ in range(4):
        out.append(m)
        out.append(np.fliplr(m))
        m = np.rot90(m)
    return out


def mask_key(mask):
    """Hashable exact encoding of a mask: (rows, cols, packed bits)."""
    return (mask.shape[0], mask.shape[1], np.packbits(mask).tobytes())


def key_mask(key):
    """Inverse of `mask_key`."""
    h, w, raw = key
    bits = np.unpackbits(np.frombuffer(raw, dtype=np.uint8), count=h * w)
    return bits.reshape(h, w).astype(bool)


def canonical(mask):
    """The smallest of the 8 transforms, so turned and mirrored agree.

    Stable for an *exact* repeat only: two footprints differing by one cell can
    still canonicalise to different turns, which is why family clustering
    compares all 8 transforms again rather than trusting this key.
    """
    return min(mask_key(v) for v in variants(mask))


# --- grouping normalised shapes into families ------------------------------

def _iou(a, b):
    inter = np.count_nonzero(a & b)
    return inter / np.count_nonzero(a | b) if inter else 0.0


def mask_pad(mask, margin):
    """`mask` on a canvas grown by `margin` cells all round."""
    h, w = mask.shape
    out = np.zeros((h + 2 * margin, w + 2 * margin), dtype=bool)
    out[margin:margin + h, margin:margin + w] = mask
    return out


def align(mask, leader, margin=ALIGN_MARGIN, floor=0.0, canvas=None,
          n_leader=None):
    """Best (iou, variant, offset) fitting `mask` over `leader`.

    The leader goes on a canvas of its own shape grown by `margin` cells all
    round, at (margin, margin), and each transform of `mask` is compared against
    a *slice* of that canvas — one small allocation per offset rather than a
    fresh canvas, which is most of what this costs. Pass `canvas` and `n_leader`
    to reuse them across many candidates against the same leader.

    Two sound prunes: only transforms whose bounding box is within `margin` of
    the leader's can win, which discards half the transforms of any non-square
    shape; and since the overlap can never exceed the smaller of the two areas
    over the larger, a transform whose area ratio is already below `floor` (or
    below the best found so far) cannot win either.
    """
    hb, wb = leader.shape
    if canvas is None:
        canvas = mask_pad(leader, margin)
    if n_leader is None:
        n_leader = int(np.count_nonzero(leader))
    height, width = canvas.shape
    best = (floor, None, None)
    for var in variants(mask):
        hv, wv = var.shape
        if abs(hv - hb) > margin or abs(wv - wb) > margin:
            continue
        nv = int(np.count_nonzero(var))
        if min(nv, n_leader) < best[0] * max(nv, n_leader):
            continue
        for dz in range(height - hv + 1):
            for dx in range(width - wv + 1):
                inter = int(np.count_nonzero(var & canvas[dz:dz + hv,
                                                          dx:dx + wv]))
                score = inter / (nv + n_leader - inter) if inter else 0.0
                if score > best[0]:
                    best = (score, var, (dz, dx))
    return (0.0, None, None) if best[1] is None else best


def largest_blob(mask):
    """Largest 4-connected component, so a consensus never comes out in pieces."""
    todo = {(int(z), int(x)) for z, x in zip(*np.nonzero(mask), strict=True)}
    best = set()
    while todo:
        stack = [todo.pop()]
        blob = set(stack)
        while stack:
            z, x = stack.pop()
            for nb in ((z - 1, x), (z + 1, x), (z, x - 1), (z, x + 1)):
                if nb in todo:
                    todo.discard(nb)
                    blob.add(nb)
                    stack.append(nb)
        if len(blob) > len(best):
            best = blob
    out = np.zeros_like(mask)
    for z, x in best:
        out[z, x] = True
    return out


class Family:
    """One repeated shape: a normalised leader, and the real sizes it came in.

    `sizes` is what makes this useful. The family says "2:1 rectangle"; the
    sizes say the city has 4,000 of them at 11 x 6 cells and 900 at 14 x 7, and
    only a concrete size can be written out as a model.
    """

    def __init__(self, leader):
        self.leader = leader              # normalised mask
        self.members = 0                  # buildings
        self.shapes = 0                   # distinct normalised shapes folded in
        self.sizes = Counter()            # (cols, rows) in cells -> buildings
        self.exact = defaultdict(int)     # exact mask key -> buildings
        self.area_sum = 0.0

    def add(self, exact_keys, areas):
        """Fold one normalised shape's real masks in. `exact_keys` is key -> n.

        `areas` holds each exact key's *summed* area over the whole extract, and
        an exact mask normalises to exactly one shape, so no key is ever counted
        into two families and the running total stays a plain sum.
        """
        self.shapes += 1
        for key, n in exact_keys.items():
            h, w = key[0], key[1]
            self.members += n
            self.sizes[(w, h)] += n
            self.exact[key] += n
            self.area_sum += areas.get(key, 0.0)

    @property
    def mean_area(self):
        return self.area_sum / max(self.members, 1)

    def modal_size(self):
        """The cell dimensions most of the family's buildings really had."""
        return self.sizes.most_common(1)[0][0] if self.sizes else None

    def consensus(self, size=None, margin=ALIGN_MARGIN):
        """The cells a majority of the members at `size` agree on.

        Voting over the real masks of one size, not over the normalised shape:
        the normalised leader has the family's silhouette but none of its
        metres, and it is metres that have to line up with a voxel grid.
        """
        size = size or self.modal_size()
        if size is None:
            return None, 0
        want = [(k, n) for k, n in self.exact.items()
                if (k[1], k[0]) == size]
        if not want:
            return None, 0
        want.sort(key=lambda kn: -kn[1])
        leader = key_mask(want[0][0])
        votes = mask_pad(leader, margin).astype(np.int64) * want[0][1]
        weight = want[0][1]
        for key, n in want[1:]:
            _, var, off = align(key_mask(key), leader, margin)
            if var is None:
                continue
            votes[off[0]:off[0] + var.shape[0],
                  off[1]:off[1] + var.shape[1]] += var * n
            weight += n
        blob = largest_blob(votes * 2 >= weight)
        return _trim(blob), weight


def families(counts, areas, iou_join=IOU_JOIN, min_count=MIN_SHAPE_COUNT,
             max_candidates=MAX_CANDIDATES, verbose=True):
    """Group normalised shapes into families, commonest candidate leading.

    `counts` is keyed on (normalised key, exact key), which is what lets a family
    carry both its silhouette and the real sizes behind it. Taking the commonest
    shape as each leader is what makes the result stable: a family is anchored on
    a shape the city really has many of, not on whichever near-miss came first.

    Only shapes seen at least `min_count` times are grouped. A city's tail is
    enormous and almost entirely singletons — 66k of Hamburg's 74k distinct
    shapes occur exactly once — and grouping is quadratic in candidates, so
    including them costs hours and cannot change which shapes come out on top.
    What it does change is `share`, hence `coverage` in the returned stats.
    """
    per_shape = defaultdict(Counter)
    for (norm_key, exact_key), n in counts.items():
        per_shape[norm_key][exact_key] += n
    ranked = sorted(per_shape.items(), key=lambda kv: -sum(kv[1].values()))
    total = sum(counts.values())
    cut = [kv for kv in ranked[:max_candidates]
           if sum(kv[1].values()) >= min_count]

    out = []
    canvases = []                  # padded leader + its filled count, reused
    buckets = defaultdict(list)    # sorted dims -> leader indices
    t0 = time.time()
    for n, (norm_key, exact_keys) in enumerate(cut):
        if verbose and n and n % 200 == 0:
            print(f'\r  grouping {n:,}/{len(cut):,} shapes, '
                  f'{len(out):,} families', end='', flush=True)
        mask = key_mask(norm_key)
        h, w = mask.shape
        n_mask = int(np.count_nonzero(mask))
        # Only leaders of a similar bounding box can reach the threshold, and
        # sorted dims survive every transform `align` will try. Dims alone are a
        # weak filter here — nearly every normalised shape is 20 by something —
        # so filter on filled cells too: overlap can never exceed the smaller
        # area over the larger, so a leader outside that band cannot match.
        near = set()
        for dh in range(-ALIGN_MARGIN, ALIGN_MARGIN + 1):
            for dw in range(-ALIGN_MARGIN, ALIGN_MARGIN + 1):
                near.update(buckets.get(tuple(sorted((h + dh, w + dw))), ()))

        best = (0.0, None)
        for fi in near:
            canvas, n_leader = canvases[fi]
            if min(n_mask, n_leader) < iou_join * max(n_mask, n_leader):
                continue
            score, _, _ = align(mask, out[fi].leader, floor=max(best[0],
                                                                iou_join),
                                canvas=canvas, n_leader=n_leader)
            if score > best[0]:
                best = (score, fi)
        if best[0] >= iou_join:
            out[best[1]].add(exact_keys, areas)
        else:
            fam = Family(mask)
            fam.add(exact_keys, areas)
            out.append(fam)
            canvases.append((mask_pad(mask, ALIGN_MARGIN), n_mask))
            buckets[tuple(sorted((h, w)))].append(len(out) - 1)

    out.sort(key=lambda f: f.members, reverse=True)
    grouped = sum(f.members for f in out)
    if verbose:
        print(f'\r  grouped {len(cut):,} of {len(ranked):,} shapes into '
              f'{len(out):,} families in {time.time() - t0:.1f}s — '
              f'{100 * grouped / max(total, 1):.1f}% of footprints '
              f'(shapes seen < {min_count} times are skipped)')
    return out


# --- reading the extract ---------------------------------------------------

def _cache_key(path, cell):
    st = os.stat(path)
    raw = (f'{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}|'
           f'{cell:.4f}|{SUPERSAMPLE}|{MIN_AREA_M2}|{MAX_AREA_M2}|'
           f'{MAX_CELLS}|{MAX_EDGES}|{FRAME_TOL}|{NORM_LONG}|'
           f'{FOOTPRINT_VERSION}')
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def collect(path, cell=CELL, cache_dir='cache', use_cache=True, verbose=True):
    """Count (normalised shape, exact mask) pairs over the whole extract.

    Returns (Counter, dict exact key -> summed area, buildings seen). Cached,
    because the pass costs minutes while the thresholds worth experimenting with
    are all on the grouping side of it.
    """
    cache_file = None
    if use_cache and cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        cache_file = os.path.join(cache_dir,
                                  f'footprints-{_cache_key(path, cell)}.pkl')
        if os.path.exists(cache_file):
            with open(cache_file, 'rb') as fh:
                data = pickle.load(fh)
            if verbose:
                print(f'  footprints from cache: {data["seen"]:,} buildings, '
                      f'{data["shapes"]:,} distinct normalised shapes')
            return Counter(data['counts']), data['areas'], data['seen']

    counts = Counter()
    areas = {}
    seen = kept = 0
    t0 = time.time()
    fp = (osmium.FileProcessor(path)
          .with_areas()
          .with_locations()
          .with_filter(osmium.filter.KeyFilter(*KEYS)))

    for obj in fp:
        if not obj.is_area():
            continue
        tg = obj.tags
        if not T.is_building(tg) or T.is_underground(tg):
            continue
        seen += 1
        if verbose and seen % 25_000 == 0:
            print(f'\r  {seen:,} buildings, {kept:,} usable', end='', flush=True)
        try:
            rings = list(obj.outer_rings())
        except Exception:                                  # noqa: BLE001
            continue
        if not rings:
            continue
        outer = rings[0]
        # one local projection per building: a footprint is metres across, so
        # anchoring on its own first node makes the distortion unmeasurable
        proj = Projection(outer[0].lon, outer[0].lat)
        pts = proj.forward(*np.array([(n.lon, n.lat) for n in outer]).T)
        try:
            holes = [proj.forward(*np.array([(n.lon, n.lat) for n in h]).T)
                     for h in obj.inner_rings(outer)]
        except Exception:                                  # noqa: BLE001
            holes = []

        got = footprint_mask(pts, holes, cell)
        if got is None:
            continue
        mask, area, span = got
        norm = normalise(mask, span)
        if norm is None:
            continue
        exact_key = canonical(mask)
        counts[(canonical(norm), exact_key)] += 1
        areas[exact_key] = areas.get(exact_key, 0.0) + area
        kept += 1

    shapes = len({k[0] for k in counts})
    if verbose:
        print(f'\r  {seen:,} buildings, {kept:,} usable, {shapes:,} distinct '
              f'normalised shapes in {time.time() - t0:.1f}s')
    if cache_file:
        with open(cache_file, 'wb') as fh:
            pickle.dump({'counts': dict(counts), 'areas': areas, 'seen': seen,
                         'shapes': shapes}, fh)
    return counts, areas, seen


# --- output ----------------------------------------------------------------

def footprint_voxels(mask, hue=FOOTPRINT_HUE):
    """One voxel layer at y=0 for a mask, min corner at the origin."""
    return {(int(x), 0, int(z)): hue
            for z, x in zip(*np.nonzero(mask), strict=True)}


def load_cells(path):
    """The ground a footprint model covers, as {(x, z)}.

    Flattened rather than sliced at y=0, so any model can serve as a footprint —
    pointing this at a finished house gives you its outline to rebuild on.
    """
    return {(x, z) for x, _, z in voxel.load(path)}


def list_models(out_dir=OUT_DIR):
    """Footprint models on disk, commonest first, each with its index entry.

    Falls back to reading the models themselves when there is no index.json, so
    a hand-made or hand-edited footprint in the directory is still offered.
    """
    try:
        names = sorted(f for f in os.listdir(out_dir)
                       if f.startswith('footprint-') and f.endswith('.json'))
    except OSError:
        return []

    meta = {}
    try:
        with open(os.path.join(out_dir, 'index.json')) as fh:
            index = json.load(fh)
        for fam in index.get('families', []):
            for s in fam.get('sizes', []):
                meta[s['file']] = {'buildings': s.get('buildings'),
                                   'family': fam.get('rank'),
                                   'metres': s.get('metres')}
    except (OSError, ValueError, KeyError):
        pass

    out = []
    for name in names:
        path = os.path.join(out_dir, name)
        try:
            cells = load_cells(path)
        except (OSError, ValueError, KeyError):
            continue
        if not cells:
            continue
        info = meta.get(name, {})
        xs = [c[0] for c in cells]
        zs = [c[1] for c in cells]
        out.append({
            'file': path, 'name': name, 'cells': cells,
            'w': max(xs) - min(xs) + 1, 'h': max(zs) - min(zs) + 1,
            'buildings': info.get('buildings'), 'family': info.get('family'),
        })
    # the index's own order is by how common the shape is; keep it
    out.sort(key=lambda e: (e['buildings'] is None, -(e['buildings'] or 0)))
    return out


def write_sheet(models, path, cell, cols=6, scale=6, pad=12):
    """Contact sheet of what was written — the only way to eyeball these."""
    import pygame
    pygame.font.init()
    font = pygame.font.SysFont('dejavusansmono,consolas,monospace', 12)
    label_h = 32
    tw = max(m['mask'].shape[1] for m in models) * scale
    th = max(m['mask'].shape[0] for m in models) * scale
    cell_w, cell_h = max(tw, 130) + 2 * pad, th + 2 * pad + label_h
    rows = (len(models) + cols - 1) // cols
    surf = pygame.Surface((cols * cell_w, rows * cell_h))
    surf.fill((24, 26, 30))

    # one hue per family, so the sizes of one shape read as a group
    hues = [(150, 190, 120), (190, 165, 115), (140, 175, 200), (190, 140, 165),
            (170, 180, 130), (150, 155, 195)]
    for i, m in enumerate(models):
        ox = (i % cols) * cell_w + pad
        oy = (i // cols) * cell_h + pad
        col = hues[m['family'] % len(hues)]
        for z, x in zip(*np.nonzero(m['mask']), strict=True):
            pygame.draw.rect(surf, col, (ox + x * scale, oy + z * scale,
                                         scale - 1, scale - 1))
        lines = (f'#{m["family"]}.{m["variant"]}  {m["buildings"]:,}',
                 f'{m["metres"][0]:.0f}x{m["metres"][1]:.0f}m  '
                 f'{m["filled"]}c  fill {m["fill"]:.2f}')
        for j, line in enumerate(lines):
            surf.blit(font.render(line, True, (200, 205, 212)),
                      (ox, oy + th + pad + j * 14))
    pygame.image.save(surf, path)
    return path


def write(fams, out_dir, cell, meta, count=16, per_family=2, total=None,
          sheet=True, verbose=True):
    """Write the commonest shapes as one-layer voxel models, plus an index.

    `count` is a number of **shape families** and `per_family` the sizes each
    contributes, so at most `count * per_family` models are written. Capping the
    models instead starves the shape axis: Hamburg's first nine families are all
    rectangles at different proportions, so a cap of 24 models never reaches the
    L-shapes at all, which is the failure this whole two-axis split exists to
    avoid.
    """
    os.makedirs(out_dir, exist_ok=True)
    # share is of every footprint surveyed, not of the ones that got grouped:
    # `families` drops a rare tail, and dividing by the survivors would quietly
    # inflate every number in the index
    total = total or sum(f.members for f in fams) or 1
    models, entries = [], []

    for fam in fams:
        if len(entries) >= count:
            break
        sizes = []
        for size, n in fam.sizes.most_common():
            if len(sizes) >= per_family:
                break
            mask, weight = fam.consensus(size)
            if mask is None:
                continue
            h, w = mask.shape
            filled = int(np.count_nonzero(mask))
            name = f'footprint-{len(entries):02d}-{len(sizes)}-{w}x{h}.json'
            voxel.save(footprint_voxels(mask), os.path.join(out_dir, name))
            rec = {'variant': len(sizes), 'file': name, 'buildings': n,
                   'agreeing': weight, 'cells': [w, h],
                   'metres': [round(w * cell, 2), round(h * cell, 2)],
                   'filled': filled, 'fill': round(filled / (w * h), 3)}
            sizes.append(rec)
            models.append(dict(rec, family=len(entries), mask=mask))
        if not sizes:
            continue
        entries.append({
            'rank': len(entries), 'members': fam.members,
            'share': round(fam.members / total, 5), 'shapes': fam.shapes,
            'distinct_sizes': len(fam.sizes),
            'mean_area_m2': round(fam.mean_area, 1),
            'sizes': sizes,
        })

    index = dict(meta)
    index.update({'version': FOOTPRINT_VERSION, 'cell': cell,
                  'norm_long': NORM_LONG, 'families': entries})
    with open(os.path.join(out_dir, 'index.json'), 'w') as fh:
        json.dump(index, fh, indent=2)

    if sheet and models:
        write_sheet(models, os.path.join(out_dir, 'sheet.png'), cell)

    # A re-run with fewer families leaves the deeper models from the last one
    # behind, and then the directory says more than index.json does. Say so
    # rather than deleting: one of them may be a house somebody has started.
    written = {s['file'] for e in entries for s in e['sizes']}
    stale = sorted(f for f in os.listdir(out_dir)
                   if f.startswith('footprint-') and f.endswith('.json')
                   and f not in written)
    if stale and verbose:
        print(f'  note: {len(stale)} model(s) here are from an earlier run and '
              f'are not in index.json: {", ".join(stale[:6])}'
              + (' ...' if len(stale) > 6 else ''))
    if verbose:
        for e in entries:
            print(f'  #{e["rank"]:2d}  {e["members"]:7,} buildings '
                  f'{100 * e["share"]:5.2f}%  {e["shapes"]:5,} raw shapes, '
                  f'{e["distinct_sizes"]:4,} sizes')
            for s in e['sizes']:
                print(f'        {s["file"]:34s} {s["buildings"]:7,} '
                      f'{s["metres"][0]:5.0f}x{s["metres"][1]:<5.0f} m '
                      f'{s["filled"]:4d} cells  fill {s["fill"]:.2f}')
        print(f'  wrote {len(models)} footprints in {len(entries)} families '
              f'to {out_dir}/')
    return entries
