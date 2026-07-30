"""Voxel models: the palette, the greedy mesher, and JSON persistence.

This is the engine half of what used to be the standalone voxel editor. It
touches neither GL nor pygame on purpose: the editor (voxity.editor) draws
these models live, and the city draws the same models as buildings, so
everything they agree on has to live somewhere neither of them owns.

A model is just `{(x, y, z): hue}` over integer cells — a plain dict, unit
cubes, nothing else. Everything visible is derived from it:

    voxels -> exterior_air -> build_mesh (greedy quads) -> mesh_vertices

`mesh_vertices` emits the layout in mesh.py, so a meshed model can be
concatenated straight into a city's vertex buffer.

Colour is a (hue, value) pair at fixed saturation, and the *value* is not
stored: it is a hash of the cell position, so neighbouring cells differ
slightly and a given cell always looks the same. That hash is evaluated in the
shader rather than baked into vertices (see `shaders.voxel_value_glsl`), which
is what lets greedy meshing merge a whole wall into one quad and still draw one
brightness square per voxel.
"""

import colorsys
import json
import os
from collections import deque

import numpy as np

from .mesh import MAT_VOXEL, MeshBuilder

MODEL_DIR = 'models'
DEFAULT_MODEL = os.path.join(MODEL_DIR, 'model.json')

# --- palette ---------------------------------------------------------------

PALETTE_SAT = 0.5          # fixed medium saturation (0..1)
N_HUES = 32                # hue steps around the wheel
N_VALS = 32                # value (brightness) steps


def color_rgb(h, v):
    """RGB (0..1) for hue index h and value index v at the fixed saturation."""
    return colorsys.hsv_to_rgb((h % N_HUES) / N_HUES, PALETTE_SAT, v / (N_VALS - 1))


# The per-cell value pool: 8 levels near the middle of the value axis, half a
# step apart, so the variation is subtle. The length must stay a power of two —
# the shader reproduces `% len(VALUE_POOL)` with a bitmask.
_N_LEVELS = 8
_LEVEL_STEP = 0.5
CENTRE_VALUE = (N_VALS - 1) / 2
VALUE_POOL = [CENTRE_VALUE + _LEVEL_STEP * (k - (_N_LEVELS - 1) / 2)
              for k in range(_N_LEVELS)]              # 13.75 .. 17.25

# the hash's per-axis primes, shared verbatim with the GLSL version
HASH_PRIMES = (73856093, 19349663, 83492791)


def value_for_cell(x, y, z):
    """Deterministic value index (from VALUE_POOL) for a voxel position."""
    px, py, pz = HASH_PRIMES
    n = (int(x) * px) ^ (int(y) * py) ^ (int(z) * pz)
    return VALUE_POOL[n % len(VALUE_POOL)]


# Unit-cube faces: (outward normal, 4 corner offsets in CCW order seen from
# outside). Winding matters — back-face culling is on everywhere.
FACES = [
    ((1, 0, 0),  [(1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1)]),  # +X
    ((-1, 0, 0), [(0, 0, 1), (0, 1, 1), (0, 1, 0), (0, 0, 0)]),  # -X
    ((0, 1, 0),  [(0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0)]),  # +Y
    ((0, -1, 0), [(0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)]),  # -Y
    ((0, 0, 1),  [(1, 0, 1), (1, 1, 1), (0, 1, 1), (0, 0, 1)]),  # +Z
    ((0, 0, -1), [(0, 0, 0), (0, 1, 0), (1, 1, 0), (1, 0, 0)]),  # -Z
]

NEIGHBOURS = [n for n, _ in FACES]

# The editor's own flat-shading light: fixed direction, no shadows, no fog.
# The city lights voxels with its sun instead — only the per-cell value hash is
# common to both, and that is the part that reads as "the voxel look".
LIGHT = np.array([0.4, 1.0, 0.6]) / np.linalg.norm([0.4, 1.0, 0.6])
AMBIENT, DIFFUSE = 0.35, 0.65


def face_brightness(normal):
    return AMBIENT + DIFFUSE * max(0.0, float(np.dot(np.asarray(normal), LIGHT)))


# --- meshing ---------------------------------------------------------------
# Two passes turn the voxel dict into as few quads as possible without changing
# what you see from outside:
#
# 1. Exterior-cavity culling. Flood-fill empty space inward from just outside
#    the model's bounding box; a face survives only if the empty cell it faces
#    is reachable from outside. Faces sealing an enclosed cavity are dropped —
#    no filling, the model is not mutated, and open concavities stay.
# 2. Greedy merging. On each face plane, adjacent kept faces of the SAME HUE
#    merge into the largest rectangles possible. Only same-hue faces merge, so
#    no colour is lost; the per-cell brightness lives in the shader, so a big
#    merged quad still shows one brightness square per voxel.


def exterior_air(voxels):
    """Empty cells reachable from outside the model's padded bounding box.

    Cells not in this set are either solid or sealed inside a cavity.
    """
    if not voxels:
        return set()
    lo = [min(c[i] for c in voxels) - 1 for i in range(3)]
    hi = [max(c[i] for c in voxels) + 1 for i in range(3)]
    start = (lo[0], lo[1], lo[2])                 # a padding corner: always empty
    outside = {start}
    dq = deque([start])
    while dq:
        c = dq.popleft()
        for dx, dy, dz in NEIGHBOURS:
            nc = (c[0] + dx, c[1] + dy, c[2] + dz)
            if (lo[0] <= nc[0] <= hi[0] and lo[1] <= nc[1] <= hi[1]
                    and lo[2] <= nc[2] <= hi[2]
                    and nc not in outside and nc not in voxels):
                outside.add(nc)
                dq.append(nc)
    return outside


def _make_quad(a, u, v, s, k, cu, cv, w, ht, hue):
    """Build a merged quad on the axis-`a` plane: (normal, hue, [4 corners])."""
    plane = k + 1 if s > 0 else k
    normal = [0, 0, 0]
    normal[a] = s

    def pt(uu, vv):
        p = [0, 0, 0]
        p[a] = plane
        p[u] = uu
        p[v] = vv
        return (p[0], p[1], p[2])

    corners = [pt(cu, cv), pt(cu + w, cv), pt(cu + w, cv + ht), pt(cu, cv + ht)]
    if s < 0:                                     # keep the winding outward-facing
        corners.reverse()
    return (tuple(normal), hue, corners)


def _greedy_plane(quads, grid, a, u, v, s, k):
    """Greedily merge same-hue faces in one plane's (cu, cv) -> hue grid."""
    visited = set()
    for cu, cv in sorted(grid, key=lambda p: (p[1], p[0])):   # row-major (v, then u)
        if (cu, cv) in visited:
            continue
        hue = grid[(cu, cv)]
        w = 1
        while grid.get((cu + w, cv)) == hue and (cu + w, cv) not in visited:
            w += 1
        ht = 1
        while all(grid.get((cu + i, cv + ht)) == hue
                  and (cu + i, cv + ht) not in visited for i in range(w)):
            ht += 1
        for i in range(w):
            for j in range(ht):
                visited.add((cu + i, cv + j))
        quads.append(_make_quad(a, u, v, s, k, cu, cv, w, ht, hue))


def build_mesh(voxels):
    """Greedy-merged, exterior-culled quads: list of (normal, hue, corners)."""
    outside = exterior_air(voxels)
    quads = []
    for a in range(3):                            # normal axis
        u, v = (a + 1) % 3, (a + 2) % 3           # the two in-plane axes
        for s in (1, -1):                         # face direction
            d = [0, 0, 0]
            d[a] = s
            slices = {}                           # k -> {(cu, cv): hue} of kept faces
            for c, hue in voxels.items():
                nc = (c[0] + d[0], c[1] + d[1], c[2] + d[2])
                if nc in outside:                 # borders exterior air -> keep
                    slices.setdefault(c[a], {})[(c[u], c[v])] = hue
            for k, grid in slices.items():
                _greedy_plane(quads, grid, a, u, v, s, k)
    return quads


def mesh_vertices(quads, scale=1.0, offset=(0.0, 0.0, 0.0), mat=MAT_VOXEL):
    """Turn greedy quads into the shared vertex layout (see mesh.py).

    `scale` is the edge length of one voxel in world units and `offset` where
    the model's origin lands, so the same model can be a 1 m-per-cell prop in
    the editor and a 0.5 m-per-cell house in the city. The colour handed over
    is the *full-value* hue: `hsv_to_rgb` is linear in V at fixed H and S, so
    the shader multiplying in the per-cell value factor gives exactly what
    computing it per voxel would have.
    """
    mb = MeshBuilder()
    if not quads:
        return mb.pack()
    off = np.asarray(offset, dtype=np.float64)
    for normal, hue, corners in quads:
        c = np.asarray(corners, dtype=np.float64) * scale + off
        tris = np.array([c[0], c[1], c[2], c[0], c[2], c[3]], dtype=np.float32)
        mb.add(tris, np.asarray(normal, dtype=np.float32),
               np.asarray(color_rgb(hue, N_VALS - 1), dtype=np.float32), mat)
    return mb.pack()


def model_vertices(voxels, scale=1.0, offset=(0.0, 0.0, 0.0), mat=MAT_VOXEL):
    """Convenience: voxel dict straight to vertices."""
    return mesh_vertices(build_mesh(voxels), scale, offset, mat)


def bounds(voxels):
    """(min corner, max corner + 1) of the occupied cells, or None if empty."""
    if not voxels:
        return None
    lo = tuple(min(c[i] for c in voxels) for i in range(3))
    hi = tuple(max(c[i] for c in voxels) + 1 for i in range(3))
    return lo, hi


def centre(voxels):
    """Middle of the model's bounding box, for aiming a camera at it."""
    b = bounds(voxels)
    if b is None:
        return np.array([0.0, 0.5, 0.0])
    lo, hi = b
    return np.array([(lo[i] + hi[i]) * 0.5 for i in range(3)])


# --- persistence -----------------------------------------------------------

def block_cells(mn, size):
    """Cells of the block with min corner `mn` (skipping y < 0).

    `size` is one edge length for a cube, or an (sx, sy, sz) triple.
    """
    sx, sy, sz = size if hasattr(size, '__len__') else (size,) * 3
    for i in range(sx):
        for j in range(sy):
            for k in range(sz):
                y = mn[1] + j
                if y >= 0:
                    yield (mn[0] + i, y, mn[2] + k)


def save(voxels, path):
    data = {
        'sat': PALETTE_SAT, 'n_hues': N_HUES, 'n_vals': N_VALS,
        'voxels': [[int(x), int(y), int(z), int(hue)]
                   for (x, y, z), hue in voxels.items()],
    }
    with open(path, 'w') as fh:
        json.dump(data, fh)
    return len(voxels)


def load(path):
    """Read a model. Raises OSError if it isn't there."""
    with open(path) as fh:
        data = json.load(fh)
    voxels = {}
    for e in data['voxels']:
        if len(e) >= 5:                    # legacy sized voxel [x, y, z, size, hue]
            x, y, z, s, hue = e[:5]
            for cell in block_cells((x, y, z), s):     # expand to unit cells
                voxels[cell] = hue
        else:                              # [x, y, z, hue]
            x, y, z, hue = e
            voxels[(x, y, z)] = hue
    return voxels
