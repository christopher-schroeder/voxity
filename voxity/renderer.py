"""moderngl renderer: shadow-mapped city, sky dome, water and trees."""

import math

import moderngl
import numpy as np

from . import shaders
from .build import tree_mesh
from .camera import look_at, ortho, to_gl

SHADOW_SIZE = 2048


def _set(prog, name, value):
    u = prog.get(name, None)
    if u is None:
        return
    if isinstance(value, bytes):
        u.write(value)
    else:
        u.value = value


def sun_direction(azimuth_deg, elevation_deg):
    """Unit vector pointing at the sun. Azimuth is degrees east of north."""
    az, el = math.radians(azimuth_deg), math.radians(elevation_deg)
    return np.array([math.sin(az) * math.cos(el),
                     math.sin(el),
                     -math.cos(az) * math.cos(el)])


def sun_palette(elevation_deg):
    """Sun/sky/haze colours for a given sun height."""
    t = float(np.clip(elevation_deg / 22.0, 0.0, 1.0))
    dusk = np.array([1.62, 0.82, 0.42])
    noon = np.array([1.48, 1.38, 1.18])
    sun = dusk * (1 - t) + noon * t
    sky = np.array([0.20, 0.22, 0.33]) * (1 - t) + np.array([0.25, 0.32, 0.47]) * t
    haze = np.array([0.78, 0.60, 0.48]) * (1 - t) + np.array([0.74, 0.81, 0.89]) * t
    night = float(np.clip((elevation_deg + 6.0) / 8.0, 0.15, 1.0))
    return sun * night, sky * (0.35 + 0.65 * night), haze * (0.25 + 0.75 * night)


class Renderer:
    def __init__(self, ctx, verts, trees, extent, sun_azimuth=235.0,
                 sun_elevation=34.0):
        self.ctx = ctx
        self.extent = extent
        self.sun_azimuth = sun_azimuth
        self.sun_elevation = sun_elevation
        self.exposure = 1.0
        # haze scaled to the extract, so the surrounding skirt always
        # fades out before its edge is reached
        span = max(extent[2] - extent[0], extent[3] - extent[1])
        self.fog_density = 1.0 / (span + 1500.0)
        self.shadows = True
        self.show_trees = True
        self.time = 0.0
        # Edge length of one voxel, for anything in the buffer carrying
        # MAT_VOXEL. One uniform for the whole scene, so every voxel model in a
        # city has to be meshed at the same cell size — pass the same value to
        # `voxel.mesh_vertices(scale=...)` that you set here or the mosaic
        # stops lining up with the geometry.
        self.voxel_cell = 1.0

        self.scene_prog = ctx.program(vertex_shader=shaders.SCENE_VS,
                                      fragment_shader=shaders.SCENE_FS)
        self.depth_prog = ctx.program(vertex_shader=shaders.DEPTH_VS,
                                      fragment_shader=shaders.DEPTH_FS)
        self.sky_prog = ctx.program(vertex_shader=shaders.SKY_VS,
                                    fragment_shader=shaders.SKY_FS)

        self.vbo = ctx.buffer(verts.tobytes() if len(verts) else b'\x00' * 40)
        self.n_verts = len(verts)
        self.scene_vao = ctx.vertex_array(
            self.scene_prog,
            [(self.vbo, '3f 3f 3f 1f', 'in_pos', 'in_norm', 'in_col', 'in_mat')])
        self.scene_depth_vao = ctx.vertex_array(
            self.depth_prog, [(self.vbo, '3f 28x', 'in_pos')])

        quad = np.array([-1, -1, 3, -1, -1, 3], dtype='f4')
        self.sky_vbo = ctx.buffer(quad.tobytes())
        self.sky_vao = ctx.vertex_array(
            self.sky_prog, [(self.sky_vbo, '2f', 'in_pos')])

        self._init_trees(trees)

        self.shadow_tex = ctx.depth_texture((SHADOW_SIZE, SHADOW_SIZE))
        self.shadow_tex.compare_func = ''
        self.shadow_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self.shadow_tex.repeat_x = False
        self.shadow_tex.repeat_y = False
        self.shadow_fbo = ctx.framebuffer(depth_attachment=self.shadow_tex)
        self.light_vp = np.eye(4)
        self._shadow_key = None

    def _init_trees(self, trees):
        ctx = self.ctx
        self.n_trees = len(trees)
        pos, nrm, trunk_verts = tree_mesh()
        part = np.zeros((len(pos), 1), dtype='f4')
        part[:trunk_verts] = 1.0
        mesh = np.hstack([pos, nrm, part]).astype('f4')
        self.tree_mesh_vbo = ctx.buffer(mesh.tobytes())
        self.tree_mesh_count = len(pos)
        data = trees if self.n_trees else np.zeros((1, 5), dtype='f4')
        self.tree_inst_vbo = ctx.buffer(np.ascontiguousarray(data, dtype='f4').tobytes())

        self.tree_prog = ctx.program(vertex_shader=shaders.TREE_VS,
                                     fragment_shader=shaders.TREE_FS)
        self.tree_depth_prog = ctx.program(vertex_shader=shaders.TREE_DEPTH_VS,
                                           fragment_shader=shaders.DEPTH_FS)
        self.tree_vao = ctx.vertex_array(self.tree_prog, [
            (self.tree_mesh_vbo, '3f 3f 1f', 'in_pos', 'in_norm', 'in_part'),
            (self.tree_inst_vbo, '2f 1f 1f 1f/i',
             'in_offset', 'in_height', 'in_radius', 'in_tint'),
        ])
        self.tree_depth_vao = ctx.vertex_array(self.tree_depth_prog, [
            (self.tree_mesh_vbo, '3f 16x', 'in_pos'),
            (self.tree_inst_vbo, '2f 1f 1f 4x/i',
             'in_offset', 'in_height', 'in_radius'),
        ])

    def release(self):
        """Give the GPU objects back.

        Only matters because the region can be re-picked without restarting:
        without this, every trip back to the overview map would leak a whole
        city's worth of vertex buffers.
        """
        for obj in (self.scene_vao, self.scene_depth_vao, self.sky_vao,
                    self.tree_vao, self.tree_depth_vao,
                    self.vbo, self.sky_vbo, self.tree_mesh_vbo,
                    self.tree_inst_vbo,
                    self.shadow_fbo, self.shadow_tex,
                    self.scene_prog, self.depth_prog, self.sky_prog,
                    self.tree_prog, self.tree_depth_prog):
            obj.release()

    # -- lighting ------------------------------------------------------------

    def _light_matrix(self):
        minx, minz, maxx, maxz = self.extent
        cx, cz = 0.5 * (minx + maxx), 0.5 * (minz + maxz)
        radius = 0.5 * math.hypot(maxx - minx, maxz - minz) + 150.0
        centre = np.array([cx, 40.0, cz])
        d = sun_direction(self.sun_azimuth, max(self.sun_elevation, 4.0))
        eye = centre + d * radius * 2.0
        up = (0.0, 0.0, -1.0) if abs(d[1]) > 0.98 else (0.0, 1.0, 0.0)
        proj = ortho(-radius, radius, -radius, radius, 1.0, radius * 4.0)
        return proj @ look_at(eye, centre, up), radius

    def _lighting_uniforms(self, prog, eye):
        sun_col, sky_col, haze = sun_palette(self.sun_elevation)
        _set(prog, 'u_sun', tuple(sun_direction(self.sun_azimuth, self.sun_elevation)))
        _set(prog, 'u_sun_col', tuple(sun_col))
        _set(prog, 'u_sky_col', tuple(sky_col))
        _set(prog, 'u_bounce_col', (0.115, 0.11, 0.10))
        _set(prog, 'u_fog_col', tuple(haze))
        _set(prog, 'u_fog_density', self.fog_density)
        _set(prog, 'u_eye', tuple(float(v) for v in eye))
        _set(prog, 'u_time', self.time)
        _set(prog, 'u_exposure', self.exposure)
        _set(prog, 'u_shadow_strength', 0.92 if self.shadows else 0.0)
        _set(prog, 'u_shadow', 0)
        _set(prog, 'u_shadow_texel', (1.0 / SHADOW_SIZE, 1.0 / SHADOW_SIZE))
        _set(prog, 'u_light_vp', to_gl(self.light_vp))
        _set(prog, 'u_voxel_cell', self.voxel_cell)

    # -- passes --------------------------------------------------------------

    def _shadow_pass(self):
        # the map only changes when the sun does, so keep the last one
        key = (round(self.sun_azimuth, 2), round(self.sun_elevation, 2),
               self.show_trees)
        if key == self._shadow_key:
            return
        self._shadow_key = key
        self.light_vp, _ = self._light_matrix()
        self.shadow_fbo.use()
        self.shadow_fbo.clear(depth=1.0)
        self.ctx.enable(moderngl.DEPTH_TEST)
        self.ctx.disable(moderngl.CULL_FACE)
        self.ctx.polygon_offset = (2.5, 6.0)
        buf = to_gl(self.light_vp)
        _set(self.depth_prog, 'u_light_vp', buf)
        if self.n_verts:
            self.scene_depth_vao.render(moderngl.TRIANGLES, vertices=self.n_verts)
        if self.show_trees and self.n_trees:
            _set(self.tree_depth_prog, 'u_light_vp', buf)
            self.tree_depth_vao.render(moderngl.TRIANGLES,
                                       vertices=self.tree_mesh_count,
                                       instances=self.n_trees)
        self.ctx.polygon_offset = (0.0, 0.0)

    def render(self, target, camera, aspect, dt=0.0):
        self.time += dt
        if self.shadows:
            self._shadow_pass()

        view = camera.view()
        proj = camera.projection(aspect)
        vp = proj @ view
        eye = camera.pos

        target.use()
        self.ctx.enable(moderngl.DEPTH_TEST | moderngl.CULL_FACE)
        self.ctx.cull_face = 'back'
        self.ctx.depth_func = '<='
        target.clear(0.0, 0.0, 0.0, 1.0, depth=1.0)

        self.shadow_tex.use(0)

        _set(self.scene_prog, 'u_vp', to_gl(vp))
        self._lighting_uniforms(self.scene_prog, eye)
        if self.n_verts:
            self.scene_vao.render(moderngl.TRIANGLES, vertices=self.n_verts)

        if self.show_trees and self.n_trees:
            _set(self.tree_prog, 'u_vp', to_gl(vp))
            self._lighting_uniforms(self.tree_prog, eye)
            self.tree_vao.render(moderngl.TRIANGLES,
                                 vertices=self.tree_mesh_count,
                                 instances=self.n_trees)

        # sky last, only where nothing was drawn
        self.ctx.disable(moderngl.CULL_FACE)
        self.ctx.depth_mask = False
        _set(self.sky_prog, 'u_inv_vp', to_gl(np.linalg.inv(vp)))
        _set(self.sky_prog, 'u_eye', tuple(float(v) for v in eye))
        self._lighting_uniforms(self.sky_prog, eye)
        self.sky_vao.render(moderngl.TRIANGLES, vertices=3)
        self.ctx.depth_mask = True
