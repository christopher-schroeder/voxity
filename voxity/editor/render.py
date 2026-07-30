"""The editor's GL side: the voxel mesh, the floor grid and the hover boxes.

Deliberately thin. The mesh it uploads is the shared layout from mesh.py, and
the only reason it exists at all rather than reusing `renderer.Renderer` is
lighting: the editor wants its own fixed light and no shadows, fog or tone
mapping, so a hue on screen is the hue in the palette. Swap this program for
`SCENE_VS`/`SCENE_FS` and the very same buffer renders as part of a city.
"""

import moderngl
import numpy as np

from .. import shaders, voxel
from ..camera import to_gl

# The floor grid is drawn in **metres**, not cells: at a quarter-metre cell a
# line per cell over a house-sized area is thousands of lines that read as a
# solid grey sheet, and the thing you actually want to judge against is a metre.
GRID_HALF_M = 16.0      # the grid spans this far either side of the origin
GRID_STEP_M = 1.0       # one line per metre
GRID_COL = (0.30, 0.30, 0.34)
AXIS_X_COL = (0.55, 0.30, 0.30)
AXIS_Z_COL = (0.30, 0.30, 0.55)

# The chosen footprint: its cell edges, and the boundary you cannot build past.
FOOT_IN_COL = (0.32, 0.46, 0.38)
FOOT_EDGE_COL = (0.45, 0.95, 0.60)

WIRE_COL = (0.06, 0.06, 0.08)   # the triangle overlay, dark against every hue


def _grid_lines(cell=None):
    """Line segments for the floor grid, with brighter centre axes.

    Positions are in cells, because that is what the model is in; the spacing
    comes from metres.
    """
    cell = voxel.CELL_M if cell is None else cell
    half = max(1, round(GRID_HALF_M / cell))
    step = max(1, round(GRID_STEP_M / cell))
    seg = []
    for i in range(-half, half + 1, step):
        if i == 0:
            continue
        seg += [(i, 0, -half, *GRID_COL), (i, 0, half, *GRID_COL)]
        seg += [(-half, 0, i, *GRID_COL), (half, 0, i, *GRID_COL)]
    seg += [(-half, 0, 0, *AXIS_X_COL), (half, 0, 0, *AXIS_X_COL)]
    seg += [(0, 0, -half, *AXIS_Z_COL), (0, 0, half, *AXIS_Z_COL)]
    return np.array(seg, dtype='f4')


def footprint_lines(cells, inner=FOOT_IN_COL, edge=FOOT_EDGE_COL, y=0.0):
    """Line segments for a footprint: cell edges, then its outer boundary.

    Returns (inside, border) as two arrays so the caller can draw them at
    different depths — the mesh should bury the interior as the building rises,
    while the border has to stay legible, since it is the limit you are building
    against.
    """
    cells = set(cells)
    inside, border = [], []
    for x, z in cells:
        # each cell contributes its -x and -z edge, plus a +x/+z edge only
        # where there is no neighbour to contribute it instead: every edge is
        # emitted exactly once, and a boundary edge is one with no neighbour
        for dx, dz, a, b in ((-1, 0, (x, z), (x, z + 1)),
                             (1, 0, (x + 1, z), (x + 1, z + 1)),
                             (0, -1, (x, z), (x + 1, z)),
                             (0, 1, (x, z + 1), (x + 1, z + 1))):
            if (x + dx, z + dz) in cells:
                if dx > 0 or dz > 0:            # shared: let one side emit it
                    continue
                seg, col = inside, inner
            else:
                seg, col = border, edge
            seg += [(a[0], y, a[1], *col), (b[0], y, b[1], *col)]
    return (np.array(inside, dtype='f4').reshape(-1, 6),
            np.array(border, dtype='f4').reshape(-1, 6))


def box_lines(mn, size, col):
    """The 12 edges of the box with min corner `mn` and edge lengths `size`.

    `size` is one edge length for a cube, or an (sx, sy, sz) triple.
    """
    sx, sy, sz = size if hasattr(size, '__len__') else (size,) * 3
    x, y, z = mn
    c = [(x + i * sx, y + j * sy, z + k * sz)
         for i in (0, 1) for j in (0, 1) for k in (0, 1)]
    # index bits are (i, j, k); an edge joins corners differing in one bit
    edges = [(a, a ^ b) for a in range(8) for b in (4, 2, 1) if not a & b]
    return np.array([(*c[p], *col) for e in edges for p in e], dtype='f4')


class EditorRenderer:
    def __init__(self, ctx):
        self.ctx = ctx
        self.voxel_prog = ctx.program(vertex_shader=shaders.VOXEL_VS,
                                      fragment_shader=shaders.VOXEL_FS)
        self.voxel_prog['u_light'].value = tuple(float(v) for v in voxel.LIGHT)
        self.voxel_prog['u_ambient'].value = voxel.AMBIENT
        self.voxel_prog['u_diffuse'].value = voxel.DIFFUSE
        self.voxel_prog['u_voxel_cell'].value = 1.0

        self.line_prog = ctx.program(vertex_shader=shaders.LINE_VS,
                                     fragment_shader=shaders.LINE_FS)

        self.wire_prog = ctx.program(vertex_shader=shaders.WIRE_VS,
                                     fragment_shader=shaders.WIRE_FS)
        self.wire_prog['u_col'].value = WIRE_COL

        self.vbo = None
        self.vao = None
        self.wire_vao = None
        self.n_verts = 0

        grid = _grid_lines()
        self.grid_vbo = ctx.buffer(grid.tobytes())
        self.grid_vao = ctx.vertex_array(
            self.line_prog, [(self.grid_vbo, '3f 3f', 'in_pos', 'in_col')])
        self.grid_count = len(grid)

        # two boxes at most (hover + placement), 24 vertices each
        self.box_vbo = ctx.buffer(reserve=2 * 24 * 6 * 4, dynamic=True)
        self.box_vao = ctx.vertex_array(
            self.line_prog, [(self.box_vbo, '3f 3f', 'in_pos', 'in_col')])

        self.foot_vbo = self.foot_vao = None
        self.foot_inside = self.foot_border = 0

    def set_footprint(self, cells):
        """Replace the ground outline. `cells` is {(x, z)}, or None for none."""
        if self.foot_vao is not None:
            self.foot_vao.release()
            self.foot_vbo.release()
            self.foot_vao = self.foot_vbo = None
        self.foot_inside = self.foot_border = 0
        if not cells:
            return
        inside, border = footprint_lines(cells)
        data = np.vstack([inside, border]) if len(inside) else border
        self.foot_inside, self.foot_border = len(inside), len(border)
        self.foot_vbo = self.ctx.buffer(data.tobytes())
        self.foot_vao = self.ctx.vertex_array(
            self.line_prog, [(self.foot_vbo, '3f 3f', 'in_pos', 'in_col')])

    def upload(self, verts):
        """Replace the voxel mesh. `verts` is the (N, 10) shared layout."""
        if self.vao is not None:
            self.vao.release()
            self.wire_vao.release()
            self.vbo.release()
            self.vao = self.wire_vao = self.vbo = None
        self.n_verts = len(verts)
        if not self.n_verts:
            return
        self.vbo = self.ctx.buffer(np.ascontiguousarray(verts, dtype='f4').tobytes())
        # the material slot is skipped: this program knows everything here is voxel
        self.vao = self.ctx.vertex_array(
            self.voxel_prog,
            [(self.vbo, '3f 3f 3f 4x', 'in_pos', 'in_norm', 'in_col')])
        # a second view of the very same buffer, position only, so the overlay
        # is the triangles that were actually drawn and not a copy of them
        self.wire_vao = self.ctx.vertex_array(
            self.wire_prog, [(self.vbo, '3f 28x', 'in_pos')])

    def draw(self, camera, aspect, show_grid=True, boxes=(), show_wire=False):
        ctx = self.ctx
        vp = to_gl(camera.projection(aspect) @ camera.view())
        self.line_prog['u_vp'].write(vp)
        self.voxel_prog['u_vp'].write(vp)
        self.wire_prog['u_vp'].write(vp)

        # The grid is drawn with the depth test off, so it never writes depth
        # and the model covers it wherever the model is — exactly what the
        # fixed-function version did by disabling GL_DEPTH_TEST around it.
        ctx.disable(moderngl.DEPTH_TEST)
        if show_grid:
            self.grid_vao.render(moderngl.LINES, vertices=self.grid_count)
        # the footprint's interior goes under the model, so the building covers
        # its own ground as it rises
        if self.foot_inside:
            self.foot_vao.render(moderngl.LINES, vertices=self.foot_inside)

        ctx.enable(moderngl.DEPTH_TEST | moderngl.CULL_FACE)
        ctx.cull_face = 'back'
        if self.n_verts:
            self.vao.render(moderngl.TRIANGLES, vertices=self.n_verts)
            if show_wire:
                # The lines come from the same vertices as the surface under
                # them, so they land at exactly its depth. '<' rejects all of
                # them and '<=' keeps them only where the interpolated depth
                # happens to round the same way, which stipples every edge that
                # is not axis-aligned on screen. Pulling them a hair towards the
                # camera is the fix; the depth test stays on so the model still
                # hides its own far side.
                ctx.wireframe = True
                ctx.polygon_offset = -1.0, -1.0
                self.wire_vao.render(moderngl.TRIANGLES, vertices=self.n_verts)
                ctx.polygon_offset = 0.0, 0.0
                ctx.wireframe = False

        # the border stays on top of everything: it is the limit being built
        # against, and is useless the moment a wall hides it
        if self.foot_border:
            ctx.disable(moderngl.DEPTH_TEST)
            self.foot_vao.render(moderngl.LINES, vertices=self.foot_border,
                                 first=self.foot_inside)
            ctx.enable(moderngl.DEPTH_TEST)

        # hover / placement boxes always on top, so they read as a cursor
        if boxes:
            data = np.vstack(boxes)
            ctx.disable(moderngl.DEPTH_TEST)
            self.box_vbo.write(data.tobytes())
            self.box_vao.render(moderngl.LINES, vertices=len(data))
            ctx.enable(moderngl.DEPTH_TEST)

    def release(self):
        if self.vao is not None:
            self.vao.release()
            self.wire_vao.release()
            self.vbo.release()
        if self.foot_vao is not None:
            self.foot_vao.release()
            self.foot_vbo.release()
        for obj in (self.grid_vao, self.grid_vbo, self.box_vao, self.box_vbo,
                    self.voxel_prog, self.line_prog, self.wire_prog):
            obj.release()
