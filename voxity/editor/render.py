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

GRID_HALF = 16          # floor grid spans -GRID_HALF .. +GRID_HALF cells
GRID_COL = (0.30, 0.30, 0.34)
AXIS_X_COL = (0.55, 0.30, 0.30)
AXIS_Z_COL = (0.30, 0.30, 0.55)


def _grid_lines():
    """Line segments for the floor grid, with brighter centre axes."""
    seg = []
    for i in range(-GRID_HALF, GRID_HALF + 1):
        if i == 0:
            continue
        seg += [(i, 0, -GRID_HALF, *GRID_COL), (i, 0, GRID_HALF, *GRID_COL)]
        seg += [(-GRID_HALF, 0, i, *GRID_COL), (GRID_HALF, 0, i, *GRID_COL)]
    seg += [(-GRID_HALF, 0, 0, *AXIS_X_COL), (GRID_HALF, 0, 0, *AXIS_X_COL)]
    seg += [(0, 0, -GRID_HALF, *AXIS_Z_COL), (0, 0, GRID_HALF, *AXIS_Z_COL)]
    return np.array(seg, dtype='f4')


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

        self.vbo = None
        self.vao = None
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

    def upload(self, verts):
        """Replace the voxel mesh. `verts` is the (N, 10) shared layout."""
        if self.vao is not None:
            self.vao.release()
            self.vbo.release()
            self.vao = self.vbo = None
        self.n_verts = len(verts)
        if not self.n_verts:
            return
        self.vbo = self.ctx.buffer(np.ascontiguousarray(verts, dtype='f4').tobytes())
        # the material slot is skipped: this program knows everything here is voxel
        self.vao = self.ctx.vertex_array(
            self.voxel_prog,
            [(self.vbo, '3f 3f 3f 4x', 'in_pos', 'in_norm', 'in_col')])

    def draw(self, camera, aspect, show_grid=True, boxes=()):
        ctx = self.ctx
        vp = to_gl(camera.projection(aspect) @ camera.view())
        self.line_prog['u_vp'].write(vp)
        self.voxel_prog['u_vp'].write(vp)

        # The grid is drawn with the depth test off, so it never writes depth
        # and the model covers it wherever the model is — exactly what the
        # fixed-function version did by disabling GL_DEPTH_TEST around it.
        ctx.disable(moderngl.DEPTH_TEST)
        if show_grid:
            self.grid_vao.render(moderngl.LINES, vertices=self.grid_count)

        ctx.enable(moderngl.DEPTH_TEST | moderngl.CULL_FACE)
        ctx.cull_face = 'back'
        if self.n_verts:
            self.vao.render(moderngl.TRIANGLES, vertices=self.n_verts)

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
            self.vbo.release()
        for obj in (self.grid_vao, self.grid_vbo, self.box_vao, self.box_vbo,
                    self.voxel_prog, self.line_prog):
            obj.release()
