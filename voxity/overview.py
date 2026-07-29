"""Bake the whole .osm.pbf into one flat 2D map image, and cache it on disk.

This is the picture you see at startup: the entire extract at a glance, in a
light "zoomed out" palette, from which a square is chosen to play in.

It is deliberately *not* built on `Scene`. A Scene keeps a Python dict per
feature, which is fine for a 1 km box but not for 400k areas across a whole
region. Here the pass is streamed instead: triangles are appended to a fixed
buffer and flushed to the GPU whenever it fills, so peak memory does not grow
with the size of the extract. Layer becomes depth, which is what lets batches
be drawn in arrival order rather than sorted.

The result is a PNG plus a small sidecar recording which lon/lat box it covers.
"""

import hashlib
import os
import time

import numpy as np
import osmium

from . import shaders
from . import tags as T
from .build import _triangulate, ribbon
from .geo import Projection

MAP_VERSION = 1

# Long edge of the baked image. 4096 over the Hamburg extract is ~38 m/pixel,
# coarse enough that most buildings are sub-pixel (see BBOX_QUAD_PX) and fine
# enough to pick a 600 m square off.
MAP_LONG_EDGE = 4096

# Objects worth opening at all. Narrower than extract.KEYS: the map has no
# trees, no benches and no addresses.
KEYS = ('building', 'highway', 'railway', 'waterway', 'natural',
        'landuse', 'leisure', 'amenity', 'man_made', 'aeroway')

# A feature whose bounding box is smaller than this many pixels in *both*
# directions is drawn as its bounding box rather than triangulated. At city
# zoom that covers most of the 355k buildings in Hamburg, and the difference
# is invisible because the whole thing lands on one or two pixels.
BBOX_QUAD_PX = 1.5

# Vertices held in RAM before a flush. 6 floats each, so ~48 MB. Must stay a
# multiple of 3 or a flush would cut a triangle in half.
FLUSH_VERTS = 1_999_998


class OverviewMap:
    """A baked map image plus the geography it covers."""

    def __init__(self, pixels, size, bbox_ll):
        self.pixels = pixels          # RGB bytes, bottom-up (GL order)
        self.size = size              # (width, height) in pixels
        self.bbox_ll = bbox_ll        # (w, s, e, n) in degrees
        w, s, e, n = bbox_ll
        self.projection = Projection(0.5 * (w + e), 0.5 * (s + n))
        corners = self.projection.forward([w, e], [s, n])
        self.extent = (corners[:, 0].min(), corners[:, 1].min(),
                       corners[:, 0].max(), corners[:, 1].max())

    @property
    def metres_per_pixel(self):
        return (self.extent[2] - self.extent[0]) / self.size[0]

    def uv_to_lonlat(self, u, v):
        """Map texture coordinates (v measured from the top) to lon/lat."""
        minx, minz, maxx, maxz = self.extent
        x = minx + u * (maxx - minx)
        z = minz + v * (maxz - minz)
        return self.projection.inverse(x, z)

    def lonlat_to_uv(self, lon, lat):
        minx, minz, maxx, maxz = self.extent
        p = self.projection.forward(lon, lat)
        return ((float(p[0]) - minx) / (maxx - minx),
                (float(p[1]) - minz) / (maxz - minz))

    def metres_to_uv(self, metres):
        """Edge length in metres as a fraction of the map's width."""
        return metres / (self.extent[2] - self.extent[0])


# --- geometry accumulation --------------------------------------------------

class _Batch:
    """Triangle sink that draws itself to `fbo` whenever it fills up."""

    def __init__(self, ctx, fbo, prog):
        self.ctx = ctx
        self.fbo = fbo
        self.prog = prog
        self.vbo = ctx.buffer(reserve=FLUSH_VERTS * 6 * 4, dynamic=True)
        self.vao = ctx.vertex_array(
            prog, [(self.vbo, '2f 3f 1f', 'in_pos', 'in_col', 'in_layer')])
        self._chunks = []
        self._n = 0
        self.drawn = 0

    def add(self, tri2d, layer, rgb):
        n = len(tri2d)
        if n == 0:
            return
        block = np.empty((n, 6), dtype='f4')
        block[:, 0:2] = tri2d
        block[:, 2:5] = rgb
        block[:, 5] = layer
        self._chunks.append(block)
        self._n += n
        if self._n >= FLUSH_VERTS:
            self.flush()

    def flush(self):
        if not self._n:
            return
        import moderngl
        data = np.concatenate(self._chunks)
        self.fbo.use()
        # a single feature can be bigger than the buffer, so drain in slices
        # rather than assuming the accumulated block fits in one draw
        for start in range(0, len(data), FLUSH_VERTS):
            part = np.ascontiguousarray(data[start:start + FLUSH_VERTS])
            self.vbo.write(part.tobytes())
            self.vao.render(moderngl.TRIANGLES, vertices=len(part))
            self.drawn += len(part)
        self._chunks = []
        self._n = 0

    def release(self):
        self.vao.release()
        self.vbo.release()


def _ring_xy(ring, proj):
    pts = np.array([(n.lon, n.lat) for n in ring], dtype=np.float64)
    if len(pts) < 4:
        return None
    return proj.forward(pts[:, 0], pts[:, 1]).astype('f4')


def _bbox_quad(pts):
    """Two triangles covering a ring's bounding box."""
    x0, z0 = pts[:, 0].min(), pts[:, 1].min()
    x1, z1 = pts[:, 0].max(), pts[:, 1].max()
    return np.array([[x0, z0], [x1, z0], [x1, z1],
                     [x0, z0], [x1, z1], [x0, z1]], dtype='f4')


def _ribbon_tris(pts, width):
    r = ribbon(pts, width)
    if r is None:
        return None
    left, right = r
    n = len(left) - 1
    if n < 1:
        return None
    quads = np.empty((n, 6, 2), dtype='f4')
    quads[:, 0] = left[:-1]
    quads[:, 1] = right[:-1]
    quads[:, 2] = right[1:]
    quads[:, 3] = left[:-1]
    quads[:, 4] = right[1:]
    quads[:, 5] = left[1:]
    return quads.reshape(-1, 2)


# --- baking -----------------------------------------------------------------

def header_bbox(path):
    """The lon/lat box the extract itself claims to cover."""
    reader = osmium.io.Reader(path)
    try:
        box = reader.header().box()
        return (box.bottom_left.lon, box.bottom_left.lat,
                box.top_right.lon, box.top_right.lat)
    finally:
        reader.close()


# Fraction of features the data bbox must contain. The header box is useless
# for framing: Hamburg's extract reaches out to the Neuwerk exclave 100 km
# west, so honouring it spends 85% of the image on empty sea and drops the
# city itself to a corner. Trimming the sparse tails keeps the map on the part
# that is actually built up.
BBOX_KEEP = 0.995
BBOX_MARGIN = 0.02
BBOX_BINS = 2048


def _trim(edges, counts, keep):
    """Smallest contiguous bin range holding `keep` of the total count."""
    total = counts.sum()
    if total <= 0:
        return edges[0], edges[-1]
    target = total * keep
    cum = np.concatenate([[0.0], np.cumsum(counts)])
    lo = hi = 0
    best = len(counts)
    for i in range(len(counts) + 1):
        # advance the right edge until the window holds enough
        j = int(np.searchsorted(cum, cum[i] + target))
        if j >= len(cum):
            break
        if j - i < best:
            best, lo, hi = j - i, i, j
    # the window covers bins lo..hi-1, so its right edge is edges[hi]
    return edges[lo], edges[hi]


def data_bbox(path, keep=BBOX_KEEP, verbose=True):
    """Where the drawable features actually are, ignoring sparse outliers.

    One cheap pass that samples a single node per object — enough to build a
    histogram per axis, without materialising any geometry.
    """
    hdr = header_bbox(path)
    lon_h = np.zeros(BBOX_BINS)
    lat_h = np.zeros(BBOX_BINS)
    lon_edges = np.linspace(hdr[0], hdr[2], BBOX_BINS + 1)
    lat_edges = np.linspace(hdr[1], hdr[3], BBOX_BINS + 1)
    span_lon = max(hdr[2] - hdr[0], 1e-9)
    span_lat = max(hdr[3] - hdr[1], 1e-9)

    t0 = time.time()
    fp = (osmium.FileProcessor(path)
          .with_areas()
          .with_locations()
          .with_filter(osmium.filter.KeyFilter(*KEYS)))
    n = 0
    for obj in fp:
        loc = None
        if obj.is_area():
            try:
                ring = next(iter(obj.outer_rings()))
                loc = (ring[0].lon, ring[0].lat)
            except (StopIteration, IndexError, RuntimeError):
                continue
        elif obj.is_way():
            try:
                node = obj.nodes[0]
                if node.location.valid():
                    loc = (node.location.lon, node.location.lat)
            except (IndexError, osmium.InvalidLocationError):
                continue
        if loc is None:
            continue
        i = int((loc[0] - hdr[0]) / span_lon * BBOX_BINS)
        j = int((loc[1] - hdr[1]) / span_lat * BBOX_BINS)
        if 0 <= i < BBOX_BINS and 0 <= j < BBOX_BINS:
            lon_h[i] += 1
            lat_h[j] += 1
            n += 1

    w, e = _trim(lon_edges, lon_h, keep)
    s, nth = _trim(lat_edges, lat_h, keep)
    mw, mh = (e - w) * BBOX_MARGIN, (nth - s) * BBOX_MARGIN
    bbox = (w - mw, s - mh, e + mw, nth + mh)
    if verbose:
        print(f'  data bbox from {n:,} features in {time.time() - t0:.1f}s: '
              f'{bbox[0]:.3f},{bbox[1]:.3f} .. {bbox[2]:.3f},{bbox[3]:.3f}')
    return bbox


def bake(ctx, path, long_edge=MAP_LONG_EDGE, verbose=True):
    """Render the whole extract to an `OverviewMap`. Needs a live GL context."""
    import moderngl

    bbox = data_bbox(path, verbose=verbose)
    proj = Projection(0.5 * (bbox[0] + bbox[2]), 0.5 * (bbox[1] + bbox[3]))
    corners = proj.forward([bbox[0], bbox[2]], [bbox[1], bbox[3]])
    minx, minz = corners[:, 0].min(), corners[:, 1].min()
    maxx, maxz = corners[:, 0].max(), corners[:, 1].max()
    span_x, span_z = maxx - minx, maxz - minz

    if span_x >= span_z:
        w = long_edge
        h = max(1, round(long_edge * span_z / span_x))
    else:
        h = long_edge
        w = max(1, round(long_edge * span_x / span_z))
    m_per_px = span_x / w
    small = BBOX_QUAD_PX * m_per_px

    if verbose:
        print(f'baking overview {w}x{h} ({m_per_px:.1f} m/pixel) from {path}')

    colour = ctx.texture((w, h), 3, dtype='f1')
    depth = ctx.depth_renderbuffer((w, h))
    fbo = ctx.framebuffer(color_attachments=[colour], depth_attachment=depth)
    fbo.use()
    ctx.viewport = (0, 0, w, h)
    fbo.clear(*T.MAP_BACKGROUND, 1.0, depth=1.0)
    ctx.enable(moderngl.DEPTH_TEST)
    ctx.disable(moderngl.CULL_FACE)
    ctx.depth_func = '<='

    prog = ctx.program(vertex_shader=shaders.MAP_VS,
                       fragment_shader=shaders.MAP_FS)
    # metres -> NDC; the y scale is negative so that north (-z) is up
    prog['u_xform'].value = (2.0 / span_x, -2.0 / span_z,
                            -(minx + maxx) / span_x, (minz + maxz) / span_z)
    prog['u_layers'].value = float(T.MAP_LAYERS)

    batch = _Batch(ctx, fbo, prog)
    t0 = time.time()
    n_seen = n_area = n_way = 0

    fp = (osmium.FileProcessor(path)
          .with_areas()
          .with_locations()
          .with_filter(osmium.filter.KeyFilter(*KEYS)))

    for obj in fp:
        n_seen += 1
        if verbose and n_seen % 100_000 == 0:
            print(f'\r  {n_seen:,} objects  {batch.drawn / 3:,.0f} triangles',
                  end='', flush=True)

        if obj.is_area():
            tg = obj.tags
            building = T.is_building(tg)
            if building:
                style = T.MAP_BUILDING
            else:
                style = T.map_surface(tg)
                if style is None or T.is_underground(tg):
                    continue
            layer, rgb = style
            try:
                rings = list(obj.outer_rings())
            except Exception:                              # noqa: BLE001
                continue
            for outer in rings:
                pts = _ring_xy(outer, proj)
                if pts is None:
                    continue
                ex = pts[:, 0].max() - pts[:, 0].min()
                ez = pts[:, 1].max() - pts[:, 1].min()
                if ex < small and ez < small:
                    batch.add(_bbox_quad(pts), layer, rgb)
                    n_area += 1
                    continue
                holes = []
                if not building:                # courtyards are sub-pixel here
                    try:
                        for inner in obj.inner_rings(outer):
                            hp = _ring_xy(inner, proj)
                            if hp is not None:
                                holes.append(hp)
                    except Exception:                      # noqa: BLE001
                        holes = []
                tri = _triangulate(pts, holes)
                if tri is not None:
                    batch.add(tri, layer, rgb)
                    n_area += 1

        elif obj.is_way():
            tg = obj.tags
            style = T.map_way(tg)
            if style is None or T.is_underground(tg):
                continue
            layer, width_m, min_px, rgb = style
            try:
                pts = np.array([(n.lon, n.lat) for n in obj.nodes],
                               dtype=np.float64)
            except osmium.InvalidLocationError:
                continue
            if len(pts) < 2:
                continue
            xy = proj.forward(pts[:, 0], pts[:, 1])
            tri = _ribbon_tris(xy, max(width_m, min_px * m_per_px))
            if tri is not None:
                batch.add(tri, layer, rgb)
                n_way += 1

    batch.flush()
    ctx.finish()
    if verbose:
        print(f'\r  {n_seen:,} objects  {batch.drawn // 3:,} triangles'
              f'  ({n_area:,} areas, {n_way:,} ways) in {time.time() - t0:.1f}s')

    pixels = fbo.read(components=3)
    batch.release()
    fbo.release()
    depth.release()
    colour.release()
    prog.release()
    return OverviewMap(pixels, (w, h), bbox)


# --- caching ----------------------------------------------------------------

def map_key(path, long_edge=MAP_LONG_EDGE):
    st = os.stat(path)
    raw = (f'{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}'
           f'|{long_edge}|{MAP_VERSION}')
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _paths(path, cache_dir, long_edge):
    key = map_key(path, long_edge)
    base = os.path.join(cache_dir, f'map-{key}')
    return base + '.png', base + '.npz'


def load(path, cache_dir='cache', long_edge=MAP_LONG_EDGE, verbose=True):
    """Read a previously baked map, or None if there isn't one."""
    import pygame

    png, meta = _paths(path, cache_dir, long_edge)
    if not (os.path.exists(png) and os.path.exists(meta)):
        return None
    with np.load(meta) as data:
        bbox = tuple(float(v) for v in data['bbox'])
    surf = pygame.image.load(png)
    w, h = surf.get_size()
    # PNGs are stored top-down; the renderer wants GL's bottom-up order
    pixels = pygame.image.tostring(pygame.transform.flip(surf, False, True),
                                   'RGB')
    if verbose:
        print(f'  overview map from cache: {w}x{h}')
    return OverviewMap(pixels, (w, h), bbox)


def save(omap, path, cache_dir='cache', long_edge=MAP_LONG_EDGE, verbose=True):
    import pygame

    os.makedirs(cache_dir, exist_ok=True)
    png, meta = _paths(path, cache_dir, long_edge)
    surf = pygame.image.frombuffer(omap.pixels, omap.size, 'RGB')
    pygame.image.save(pygame.transform.flip(surf, False, True), png)
    np.savez(meta, bbox=np.array(omap.bbox_ll, dtype=np.float64))
    if verbose:
        print(f'  wrote {png}')


def load_or_bake(ctx, path, cache_dir='cache', long_edge=MAP_LONG_EDGE,
                 use_cache=True, verbose=True):
    if use_cache:
        omap = load(path, cache_dir, long_edge, verbose)
        if omap is not None:
            return omap
    omap = bake(ctx, path, long_edge, verbose)
    if use_cache:
        save(omap, path, cache_dir, long_edge, verbose)
    return omap
