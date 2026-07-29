"""Which voxel is under the cursor, and where a new block would go.

The ray comes from `camera.screen_ray`; from there it is Amanatides & Woo —
march the integer grid cell by cell, remembering which face was crossed on the
way in, and stop at the first occupied cell.
"""

import math

import numpy as np

MAX_RAY_STEPS = 256

# Every voxel is a unit cube. The brush stamps a solid E x E x E block of them,
# so the three sizes place 64 / 8 / 1 voxels.
BRUSH_EDGES = [4, 2, 1]
DEFAULT_BRUSH = len(BRUSH_EDGES) - 1      # start with the single-voxel brush


def pick(voxels, origin, direction):
    """First voxel the ray enters.

    Returns (hit_cell, entry_normal) for a hit, (None, ground_cell) when the
    ray only crosses the y=0 plane, or (None, None) when it misses everything.
    """
    o = np.asarray(origin, dtype=float)
    d = np.asarray(direction, dtype=float)
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

    # nothing hit: fall back to the ground plane at y = 0
    if d[1] < 0 and o[1] > 0:
        t = -o[1] / d[1]
        p = o + t * d
        return None, (int(np.floor(p[0])), 0, int(np.floor(p[2])))
    return None, None


def brush_block(hit_cell, info, e):
    """Min corner of the E x E x E block to stamp, or None.

    `hit_cell`/`info` are what `pick` returned. The tangential axes snap to the
    E-grid; along the hit normal the block sits on the empty side of the face
    (or on the floor, when the ray only found ground).
    """
    if hit_cell is not None:                  # placing against a voxel face
        normal = info
        a = next(i for i in range(3) if normal[i] != 0)
        mn = []
        for i in range(3):
            if i == a:
                mn.append(hit_cell[i] + 1 if normal[i] > 0 else hit_cell[i] - e)
            else:
                mn.append((hit_cell[i] // e) * e)
        return tuple(mn)
    if info is not None:                      # placing on the ground plane
        gx, _, gz = info
        return ((gx // e) * e, 0, (gz // e) * e)
    return None
