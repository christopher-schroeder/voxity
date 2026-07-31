"""Which voxel is under the cursor, and where a new block would go.

The ray comes from `camera.screen_ray`; from there it is Amanatides & Woo —
march the integer grid cell by cell, remembering which face was crossed on the
way in, and stop at the first occupied cell.
"""

import math

import numpy as np

# Steps the grid march may take. The ray is clipped to the model's own box
# first, so this bounds the model's diagonal rather than the distance the camera
# happens to be at — without that, a quarter-metre cell put a house two hundred
# cells across and a zoomed-out camera a thousand cells away, and picking simply
# stopped working past the old limit.
MAX_RAY_STEPS = 1024

# Every voxel is a unit cube. The brush stamps a solid box of them, sized per
# axis — walls and floors are the common shapes and neither is a cube.
BRUSH_MIN, BRUSH_MAX = 1, 32
DEFAULT_BRUSH = (1, 1, 1)


def _enter_box(o, d, lo, hi):
    """Distance along the ray at which it enters the box, or None if it misses.

    Slab test. Zero is returned for an origin already inside, so the march can
    start where it is.
    """
    tmin, tmax = 0.0, math.inf
    for a in range(3):
        if abs(d[a]) < 1e-12:
            if o[a] < lo[a] or o[a] > hi[a]:
                return None
            continue
        t1 = (lo[a] - o[a]) / d[a]
        t2 = (hi[a] - o[a]) / d[a]
        if t1 > t2:
            t1, t2 = t2, t1
        tmin = max(tmin, t1)
        tmax = min(tmax, t2)
        if tmin > tmax:
            return None
    return tmin


def _bounds(voxels, pad=1):
    lo = [min(c[i] for c in voxels) - pad for i in range(3)]
    hi = [max(c[i] for c in voxels) + 1 + pad for i in range(3)]
    return lo, hi


def pick(voxels, origin, direction):
    """First voxel the ray enters.

    Returns (hit_cell, entry_normal) for a hit, (None, ground_cell) when the
    ray only crosses the y=0 plane, or (None, None) when it misses everything.

    **The normal is None when the ray starts inside a solid cell** — there is no
    face it came in through. Callers must cope: `brush_block` returns None,
    because there is no empty side to stamp against. This is what the camera
    does the moment you zoom into the model, and dereferencing that None was a
    crash.
    """
    o = np.asarray(origin, dtype=float)
    d = np.asarray(direction, dtype=float)
    if voxels:
        # Skip the empty space between the camera and the model, so the step
        # budget covers the model rather than however far away you are standing.
        lo, hi = _bounds(voxels)
        t = _enter_box(o, d, lo, hi)
        if t is None:
            return None, _ground(o, d)
        if t > 0.0:
            o = o + d * t
    cell = np.floor(o).astype(int)
    step = np.where(d > 0, 1, -1)
    t_max = np.empty(3)
    t_delta = np.empty(3)
    for a in range(3):
        if d[a] == 0:
            t_max[a] = math.inf
            t_delta[a] = math.inf
        elif d[a] > 0:
            t_max[a] = (math.floor(o[a]) + 1 - o[a]) / d[a]
            t_delta[a] = 1.0 / d[a]
        else:
            t_max[a] = (o[a] - math.floor(o[a])) / -d[a]
            t_delta[a] = 1.0 / -d[a]

    normal = None
    for _ in range(MAX_RAY_STEPS):
        if tuple(cell) in voxels:
            return tuple(cell), normal
        axis = int(np.argmin(t_max))
        cell = cell.copy()
        cell[axis] += step[axis]
        normal = tuple(-step[a] if a == axis else 0 for a in range(3))
        t_max[axis] += t_delta[axis]

    return None, _ground(o, d)


def _ground(o, d):
    """Where the ray crosses y = 0, as a cell, or None if it never does."""
    if d[1] < 0 and o[1] > 0:
        p = o + (-o[1] / d[1]) * d
        return (int(np.floor(p[0])), 0, int(np.floor(p[2])))
    return None


def resize_brush(size, axis, delta):
    """`size` with `delta` added to one axis, or to all three when axis is None."""
    s = list(size)
    for i in (range(3) if axis is None else (axis,)):
        s[i] = max(BRUSH_MIN, min(BRUSH_MAX, s[i] + delta))
    return tuple(s)


def _centred(cell, extent):
    """Min corner of a run of `extent` cells centred on `cell`.

    The brush moves in single cells, not in steps of its own size, so it can
    sit anywhere — which means the cell you point at has to be *inside* it, and
    the middle is the only place that reads as a cursor. Even extents lean one
    cell to the negative side, since there is no true middle to pick.
    """
    return cell - (extent - 1) // 2


def erase_block(cell, size):
    """Min corner of the `size` block centred on `cell`, for delete and paint.

    Unlike `brush_block` this sits *on* the cell rather than beside it: you are
    acting on what is there, not adding next to it.
    """
    if cell is None:
        return None
    return (_centred(cell[0], size[0]),
            max(0, _centred(cell[1], size[1])),
            _centred(cell[2], size[2]))


def brush_block(hit_cell, info, size):
    """Min corner of the `size` (sx, sy, sz) block to stamp, or None.

    `hit_cell`/`info` are what `pick` returned. Along the hit normal the block
    sits on the empty side of the face (or on the floor, when the ray only
    found ground); the other two axes centre on the cell under the cursor.
    Centring on y is held above the floor, since `voxel.block_cells` drops
    cells below it — let it straddle and the outline would promise voxels that
    never appear, which is what aiming a tall brush at a low wall does.
    """
    if hit_cell is not None:                  # placing against a voxel face
        normal = info
        if normal is None:                    # started inside solid: no face
            return None
        a = next(i for i in range(3) if normal[i] != 0)
        mn = []
        for i in range(3):
            if i == a:
                mn.append(hit_cell[i] + 1 if normal[i] > 0
                          else hit_cell[i] - size[i])
            elif i == 1:
                mn.append(max(0, _centred(hit_cell[i], size[i])))
            else:
                mn.append(_centred(hit_cell[i], size[i]))
        return tuple(mn)
    if info is not None:                      # placing on the ground plane
        gx, _, gz = info
        return (_centred(gx, size[0]), 0, _centred(gz, size[2]))
    return None
