"""The triangle-soup vertex layout, and the accumulator that produces it.

Vertex layout: position(3f) normal(3f) colour(3f) material(1f) — declared here,
consumed by `Renderer.scene_vao`, read by `SCENE_VS`.

Two very different things build this soup: `build.py` extrudes OSM footprints,
and `voxel.py` greedy-meshes voxel models from the editor. They share this
module so a vertex made by one is interchangeable with a vertex made by the
other — that is the whole reason a voxel house can be dropped into a city.
"""

import numpy as np

# Material slot. `SCENE_FS` switches on this, from the top down, so the values
# have to stay distinct by more than 0.5 and any new one needs its own branch
# there — an unhandled material falls through to matte.
MAT_MATTE = 0.0        # ordinary lit surface
MAT_WATER = 1.0        # ripple normal + specular
MAT_VOXEL = 2.0        # per-cell brightness mosaic (the editor's look)


class MeshBuilder:
    def __init__(self):
        self._pos = []
        self._nrm = []
        self._col = []
        self._mat = []
        self.count = 0

    def add(self, pos, nrm, col, mat):
        """Append triangles, re-winding each one to agree with its normal."""
        n = len(pos)
        if n == 0:
            return
        pos = np.array(pos, dtype=np.float32, copy=True)
        nrm = np.asarray(nrm, dtype=np.float32)
        col = np.asarray(col, dtype=np.float32)
        if nrm.ndim == 1:
            nrm = np.tile(nrm, (n, 1))
        else:
            nrm = np.array(nrm, dtype=np.float32, copy=True)
        if col.ndim == 1:
            col = np.tile(col, (n, 1))
        else:
            col = np.array(col, dtype=np.float32, copy=True)

        tp, tn, tc = (a.reshape(-1, 3, 3) for a in (pos, nrm, col))
        face = np.cross(tp[:, 1] - tp[:, 0], tp[:, 2] - tp[:, 0])
        flip = (face * tn[:, 0]).sum(axis=1) < 0.0
        if flip.any():
            order = [0, 2, 1]
            tp[flip] = tp[flip][:, order]
            tn[flip] = tn[flip][:, order]
            tc[flip] = tc[flip][:, order]

        self._pos.append(np.ascontiguousarray(pos))
        self._nrm.append(np.ascontiguousarray(nrm))
        self._col.append(np.ascontiguousarray(col))
        self._mat.append(np.full((n, 1), mat, dtype=np.float32))
        self.count += n

    def pack(self):
        if not self._pos:
            return np.zeros((0, 10), dtype=np.float32)
        return np.hstack([np.concatenate(self._pos),
                          np.concatenate(self._nrm),
                          np.concatenate(self._col),
                          np.concatenate(self._mat)]).astype(np.float32)


def orient_triangles(pos, nrm):
    """Re-wind triangles so their face normal agrees with the shading normal."""
    tp = pos.reshape(-1, 3, 3)
    tn = nrm.reshape(-1, 3, 3)
    face = np.cross(tp[:, 1] - tp[:, 0], tp[:, 2] - tp[:, 0])
    flip = (face * tn[:, 0]).sum(axis=1) < 0.0
    if flip.any():
        tp[flip] = tp[flip][:, [0, 2, 1]]
        tn[flip] = tn[flip][:, [0, 2, 1]]
    return pos, nrm
