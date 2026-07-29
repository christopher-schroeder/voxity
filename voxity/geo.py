"""Geodesy helpers: local metric projection and bbox clipping."""

import math

import numpy as np

R_EARTH = 6378137.0


class Projection:
    """Equirectangular projection around an anchor point, in metres.

    x -> east, z -> south (so north is -z), which matches a y-up renderer
    looking down the -z axis by default.
    """

    def __init__(self, lon0, lat0):
        self.lon0 = lon0
        self.lat0 = lat0
        self.mx = math.radians(1.0) * R_EARTH * math.cos(math.radians(lat0))
        self.mz = math.radians(1.0) * R_EARTH

    def forward(self, lon, lat):
        lon = np.asarray(lon, dtype=np.float64)
        lat = np.asarray(lat, dtype=np.float64)
        return np.stack([(lon - self.lon0) * self.mx,
                         -(lat - self.lat0) * self.mz], axis=-1)

    def inverse(self, x, z):
        return (x / self.mx + self.lon0, -z / self.mz + self.lat0)


def square_bbox(lon, lat, size_m):
    """Axis-aligned lon/lat box approximating a `size_m` square around a point."""
    half = size_m * 0.5
    dlat = math.degrees(half / R_EARTH)
    dlon = math.degrees(half / (R_EARTH * math.cos(math.radians(lat))))
    return (lon - dlon, lat - dlat, lon + dlon, lat + dlat)


def bbox_overlaps(a, b):
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


# --- clipping ---------------------------------------------------------------

def clip_polygon(pts, bbox):
    """Sutherland-Hodgman clip of a ring (Nx2 array) against an AABB."""
    minx, miny, maxx, maxy = bbox
    poly = np.asarray(pts, dtype=np.float64)
    for axis, limit, keep_greater in ((0, minx, True), (0, maxx, False),
                                      (1, miny, True), (1, maxy, False)):
        if len(poly) < 3:
            return None
        coord = poly[:, axis]
        inside = coord >= limit if keep_greater else coord <= limit
        if inside.all():
            continue
        if not inside.any():
            return None
        nxt = np.roll(poly, -1, axis=0)
        nxt_in = np.roll(inside, -1)
        out = []
        for i in range(len(poly)):
            cur_in, nex_in = inside[i], nxt_in[i]
            if cur_in:
                out.append(poly[i])
            if cur_in != nex_in:
                p, q = poly[i], nxt[i]
                d = q[axis] - p[axis]
                t = 0.0 if d == 0 else (limit - p[axis]) / d
                out.append(p + (q - p) * t)
        poly = np.array(out, dtype=np.float64)
    return poly if len(poly) >= 3 else None


def _liang_barsky(p, q, bbox):
    minx, miny, maxx, maxy = bbox
    dx, dy = q[0] - p[0], q[1] - p[1]
    t0, t1 = 0.0, 1.0
    for num, den in ((p[0] - minx, -dx), (maxx - p[0], dx),
                     (p[1] - miny, -dy), (maxy - p[1], dy)):
        if den == 0:
            if num < 0:
                return None
            continue
        t = num / den
        if den < 0:
            if t > t1:
                return None
            t0 = max(t0, t)
        else:
            if t < t0:
                return None
            t1 = min(t1, t)
    return (p + (q - p) * t0, p + (q - p) * t1)


def clip_polyline(pts, bbox):
    """Clip a polyline to an AABB, returning a list of contiguous chains."""
    pts = np.asarray(pts, dtype=np.float64)
    minx, miny, maxx, maxy = bbox
    inside = ((pts[:, 0] >= minx) & (pts[:, 0] <= maxx) &
              (pts[:, 1] >= miny) & (pts[:, 1] <= maxy))
    if inside.all():
        return [pts]

    chains, cur = [], []
    for i in range(len(pts) - 1):
        seg = _liang_barsky(pts[i], pts[i + 1], bbox)
        if seg is None:
            if len(cur) >= 2:
                chains.append(np.array(cur))
            cur = []
            continue
        a, b = seg
        if cur and np.allclose(cur[-1], a, atol=1e-9):
            cur.append(b)
        else:
            if len(cur) >= 2:
                chains.append(np.array(cur))
            cur = [a, b]
    if len(cur) >= 2:
        chains.append(np.array(cur))
    return chains
