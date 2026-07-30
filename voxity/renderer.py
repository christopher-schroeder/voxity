"""moderngl renderer: shadow-mapped city, sky dome, water and trees.

Three passes and then a picture. The shadow map is rendered from the sun and
cached until it moves; the scene goes into an offscreen buffer in **linear
light**; ambient occlusion is derived from that buffer's depth; and POST_FS
composites the two, tone maps and grades. Nothing before the last step makes a
colour you could look at, which is deliberate — see the post-processing note in
shaders.py.
"""

import math

import moderngl
import numpy as np

from . import shaders
from .build import tree_mesh
from .camera import look_at, ortho, to_gl

SHADOW_SIZE = 2048

# The scene buffer is rendered this much larger than the window and read back
# through a LINEAR fetch, which is the whole antialiasing story. Multisampling
# cannot help here even when the window has it: the voxel mosaic and the value
# hash are computed *inside* triangles, where MSAA takes one sample. 1.5 is
# 2.25x the pixels; 1.0 turns it off.
SUPERSAMPLE = 1.5

# Ambient occlusion runs at half the scene buffer's resolution and is blurred
# 3x3 on the way out. Full resolution buys nothing visible — what it is drawing
# is a soft contact shadow — and costs four times as much.
AO_SCALE = 0.5


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
    """Sun / sky / haze / bounce colours for a given sun height.

    The top end is **daylight** — a near-white sun and a blue sky — and the
    bottom end is the golden hour. The default sun is high, so the picture is
    daylight; winding it down with `,` still gets you a sunset, which is what
    the low end is for. The blend runs out at 50 rather than at the ~25 physics
    would put it, so the warm end reaches far enough down to be reachable.

    Three of the four are *ambient*: `sky` lights an upward-facing surface,
    `bounce` lights a downward-facing one, and the mix between them by normal is
    what stops a shadowed wall reading as flat grey.
    """
    t = float(np.clip(elevation_deg / 50.0, 0.0, 1.0))

    def blend(low, high):
        return np.array(low) * (1 - t) + np.array(high) * t

    sun = blend([2.05, 1.18, 0.52], [1.62, 1.58, 1.46])
    sky = blend([0.30, 0.30, 0.36], [0.40, 0.50, 0.70])
    haze = blend([1.02, 0.68, 0.40], [0.80, 0.87, 0.98])
    # A little warm even at noon: what bounces up into an eave is the ground and
    # the brick opposite, not the sky.
    bounce = blend([0.24, 0.155, 0.10], [0.24, 0.23, 0.21])
    night = float(np.clip((elevation_deg + 6.0) / 8.0, 0.15, 1.0))
    return (sun * night, sky * (0.35 + 0.65 * night),
            haze * (0.25 + 0.75 * night), bounce * (0.3 + 0.7 * night))


class Renderer:
    def __init__(self, ctx, verts, trees, extent, sun_azimuth=215.0,
                 sun_elevation=52.0):
        self.ctx = ctx
        self.extent = extent
        self.sun_azimuth = sun_azimuth
        self.sun_elevation = sun_elevation

        # --- the grade. Every one of these is taste, not physics, and they are
        # gathered here rather than buried in POST_FS so they can be turned
        # without recompiling a shader.
        self.exposure = 1.08
        self.ao = True
        self.ao_strength = 1.15
        # Metres, and small on purpose. This is contact darkening, not a dome:
        # at the 4.5 m that looked right on a street corner, one radius covers a
        # whole tree canopy and the tree goes uniformly black.
        self.ao_radius = 1.8
        self.ao_power = 1.3           # deepens the dark end, leaves the open end
        self.ao_tint = (0.42, 0.42, 0.44)     # what an occluded fragment fades to
        # Ambient scales both halves of the sky/bounce pair, and it is the one
        # number that decides afternoon versus overcast: at 1.0 a wall in shadow
        # came out within a factor of two of one in the sun.
        self.ambient = 1.05
        self.saturation = 1.10
        self.contrast = 1.05
        self.shadow_tint = (0.95, 0.98, 1.06)
        self.light_tint = (1.03, 1.01, 0.97)
        self.vignette = 0.24
        # Not 1.0: a cast shadow that rejects every scrap of sun goes to
        # whatever ambient alone gives it, and on ground this dark that is
        # black with nothing in it.
        self.shadow_strength = 0.88
        self.supersample = SUPERSAMPLE
        # haze scaled to the extract, so the surrounding skirt always
        # fades out before its edge is reached
        span = max(extent[2] - extent[0], extent[3] - extent[1])
        self.fog_density = 1.0 / (span + 1500.0)
        self.shadows = True
        self.show_trees = True
        self.time = 0.0
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
            [(self.vbo, '3f 3f 3f 1f 3f',
              'in_pos', 'in_norm', 'in_col', 'in_mat', 'in_cell')])
        self.scene_depth_vao = ctx.vertex_array(
            self.depth_prog, [(self.vbo, '3f 40x', 'in_pos')])

        # one fullscreen triangle, shared by the sky and both post passes: three
        # vertices rather than a quad's six, so there is no diagonal seam
        quad = np.array([-1, -1, 3, -1, -1, 3], dtype='f4')
        self.sky_vbo = ctx.buffer(quad.tobytes())
        self.sky_vao = ctx.vertex_array(
            self.sky_prog, [(self.sky_vbo, '2f', 'in_pos')])

        self.ao_prog = ctx.program(vertex_shader=shaders.POST_VS,
                                   fragment_shader=shaders.AO_FS)
        self.post_prog = ctx.program(vertex_shader=shaders.POST_VS,
                                     fragment_shader=shaders.POST_FS)
        self.ao_vao = ctx.vertex_array(
            self.ao_prog, [(self.sky_vbo, '2f', 'in_pos')])
        self.post_vao = ctx.vertex_array(
            self.post_prog, [(self.sky_vbo, '2f', 'in_pos')])

        # built on first render, and rebuilt whenever the window is resized
        self.scene_fbo = self.ao_fbo = None
        self.colour_tex = self.scene_depth_tex = self.ao_tex = None
        self._buf_size = self._ao_size = (0, 0)

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

    def _release_buffers(self):
        for obj in (self.scene_fbo, self.ao_fbo, self.colour_tex,
                    self.scene_depth_tex, self.ao_tex):
            if obj is not None:
                obj.release()
        self.scene_fbo = self.ao_fbo = None
        self.colour_tex = self.scene_depth_tex = self.ao_tex = None
        self._buf_size = self._ao_size = (0, 0)

    def _ensure_buffers(self, size):
        """The offscreen scene and AO buffers, sized to the window.

        Rebuilt on resize rather than allocated once at the largest plausible
        size, because at 1.5x supersampling these are the biggest allocations in
        the renderer and a window can be dragged to any size.
        """
        w = max(1, round(size[0] * self.supersample))
        h = max(1, round(size[1] * self.supersample))
        if (w, h) == self._buf_size:
            return
        self._release_buffers()
        ctx = self.ctx
        # RGBA rather than RGB: only RGBA16F is guaranteed colour-renderable in
        # 3.3, and float rather than 8-bit because this holds linear light with
        # a bright sun in it, not a picture
        self.colour_tex = ctx.texture((w, h), 4, dtype='f2')
        self.colour_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self.colour_tex.repeat_x = self.colour_tex.repeat_y = False
        self.scene_depth_tex = ctx.depth_texture((w, h))
        self.scene_depth_tex.compare_func = ''
        self.scene_depth_tex.filter = (moderngl.NEAREST, moderngl.NEAREST)
        self.scene_depth_tex.repeat_x = self.scene_depth_tex.repeat_y = False
        self.scene_fbo = ctx.framebuffer([self.colour_tex], self.scene_depth_tex)

        aw, ah = max(1, round(w * AO_SCALE)), max(1, round(h * AO_SCALE))
        self.ao_tex = ctx.texture((aw, ah), 1, dtype='f1')
        self.ao_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self.ao_tex.repeat_x = self.ao_tex.repeat_y = False
        self.ao_fbo = ctx.framebuffer([self.ao_tex])
        self._buf_size, self._ao_size = (w, h), (aw, ah)

    def release(self):
        """Give the GPU objects back.

        Only matters because the region can be re-picked without restarting:
        without this, every trip back to the overview map would leak a whole
        city's worth of vertex buffers.
        """
        self._release_buffers()
        for obj in (self.scene_vao, self.scene_depth_vao, self.sky_vao,
                    self.ao_vao, self.post_vao,
                    self.tree_vao, self.tree_depth_vao,
                    self.vbo, self.sky_vbo, self.tree_mesh_vbo,
                    self.tree_inst_vbo,
                    self.shadow_fbo, self.shadow_tex,
                    self.scene_prog, self.depth_prog, self.sky_prog,
                    self.ao_prog, self.post_prog,
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
        sun_col, sky_col, haze, bounce = sun_palette(self.sun_elevation)
        _set(prog, 'u_sun', tuple(sun_direction(self.sun_azimuth, self.sun_elevation)))
        _set(prog, 'u_sun_col', tuple(sun_col))
        _set(prog, 'u_sky_col', tuple(sky_col * self.ambient))
        _set(prog, 'u_bounce_col', tuple(bounce * self.ambient))
        _set(prog, 'u_fog_col', tuple(haze))
        _set(prog, 'u_fog_density', self.fog_density)
        _set(prog, 'u_eye', tuple(float(v) for v in eye))
        _set(prog, 'u_time', self.time)
        _set(prog, 'u_shadow_strength',
             self.shadow_strength if self.shadows else 0.0)
        _set(prog, 'u_shadow', 0)
        _set(prog, 'u_shadow_texel', (1.0 / SHADOW_SIZE, 1.0 / SHADOW_SIZE))
        _set(prog, 'u_light_vp', to_gl(self.light_vp))

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

    def _scene_pass(self, camera, aspect):
        """Everything with geometry, into the offscreen buffer, in linear light."""
        ctx = self.ctx
        vp = camera.projection(aspect) @ camera.view()
        eye = camera.pos

        self.scene_fbo.use()
        ctx.viewport = (0, 0, *self._buf_size)
        ctx.enable(moderngl.DEPTH_TEST | moderngl.CULL_FACE)
        ctx.cull_face = 'back'
        ctx.depth_func = '<='
        self.scene_fbo.clear(0.0, 0.0, 0.0, 1.0, depth=1.0)

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
        ctx.disable(moderngl.CULL_FACE)
        ctx.depth_mask = False
        _set(self.sky_prog, 'u_inv_vp', to_gl(np.linalg.inv(vp)))
        _set(self.sky_prog, 'u_eye', tuple(float(v) for v in eye))
        self._lighting_uniforms(self.sky_prog, eye)
        self.sky_vao.render(moderngl.TRIANGLES, vertices=3)
        ctx.depth_mask = True

    def _ao_pass(self, camera, aspect):
        """Ambient occlusion from the scene buffer's own depth."""
        ctx = self.ctx
        proj = camera.projection(aspect)
        self.ao_fbo.use()
        ctx.viewport = (0, 0, *self._ao_size)
        ctx.disable(moderngl.DEPTH_TEST | moderngl.CULL_FACE)
        self.scene_depth_tex.use(1)
        _set(self.ao_prog, 'u_depth', 1)
        _set(self.ao_prog, 'u_proj', to_gl(proj))
        _set(self.ao_prog, 'u_inv_proj', to_gl(np.linalg.inv(proj)))
        _set(self.ao_prog, 'u_radius', self.ao_radius)
        # in metres, and it has to clear the depth buffer's own precision at the
        # far end of a kilometre-wide square or distant roofs self-occlude
        _set(self.ao_prog, 'u_bias', 0.05)
        _set(self.ao_prog, 'u_strength', self.ao_strength if self.ao else 0.0)
        self.ao_vao.render(moderngl.TRIANGLES, vertices=3)

    def _post_pass(self, target, size):
        """Occlude, tone map, grade, vignette — the only pass that makes colour."""
        ctx = self.ctx
        target.use()
        ctx.viewport = (0, 0, *size)
        # the composite writes every pixel, but the depth still has to be
        # cleared for whatever the caller draws over the top of it
        target.clear(0.0, 0.0, 0.0, 1.0, depth=1.0)
        self.colour_tex.use(2)
        self.ao_tex.use(3)
        _set(self.post_prog, 'u_colour', 2)
        _set(self.post_prog, 'u_ao', 3)
        _set(self.post_prog, 'u_ao_texel', (1.0 / self._ao_size[0],
                                            1.0 / self._ao_size[1]))
        _set(self.post_prog, 'u_ao_tint', tuple(self.ao_tint))
        _set(self.post_prog, 'u_ao_power', self.ao_power)
        _set(self.post_prog, 'u_exposure', self.exposure)
        _set(self.post_prog, 'u_contrast', self.contrast)
        _set(self.post_prog, 'u_saturation', self.saturation)
        _set(self.post_prog, 'u_shadow_tint', tuple(self.shadow_tint))
        _set(self.post_prog, 'u_light_tint', tuple(self.light_tint))
        _set(self.post_prog, 'u_vignette', self.vignette)
        self.post_vao.render(moderngl.TRIANGLES, vertices=3)
        ctx.enable(moderngl.DEPTH_TEST | moderngl.CULL_FACE)

    def render(self, target, camera, aspect, dt=0.0, size=None):
        """Draw a frame into `target`.

        `size` is the target's pixel size. It is a parameter rather than read
        off `target` because `ctx.screen` does not reliably know how big the
        window is — the caller resized it and does.
        """
        self.time += dt
        if self.shadows:
            self._shadow_pass()
        if size is None:
            size = target.size
        self._ensure_buffers(size)
        self._scene_pass(camera, aspect)
        self._ao_pass(camera, aspect)
        self._post_pass(target, size)
