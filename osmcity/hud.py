"""Text overlay drawn with pygame surfaces uploaded as a GL texture."""

import moderngl
import numpy as np
import pygame

from . import shaders

PAD = 8
BG = (12, 14, 18, 172)
FG = (232, 236, 240)
DIM = (150, 160, 172)


class Overlay:
    def __init__(self, ctx, size=15):
        self.ctx = ctx
        pygame.font.init()
        self.font = pygame.font.Font(None, size + 6)
        self.prog = ctx.program(vertex_shader=shaders.OVERLAY_VS,
                                fragment_shader=shaders.OVERLAY_FS)
        self.vbo = ctx.buffer(reserve=6 * 4 * 4, dynamic=True)
        self.vao = ctx.vertex_array(
            self.prog, [(self.vbo, '2f 2f', 'in_pos', 'in_uv')])
        self.tex = None
        self._key = None
        self._size = (0, 0)

    def _surface(self, lines):
        rendered = [self.font.render(t, True, DIM if dim else FG)
                    for t, dim in lines]
        w = max((s.get_width() for s in rendered), default=1) + PAD * 2
        h = sum(s.get_height() for s in rendered) + PAD * 2
        surf = pygame.Surface((w, h), pygame.SRCALPHA)
        surf.fill(BG)
        y = PAD
        for s in rendered:
            surf.blit(s, (PAD, y))
            y += s.get_height()
        return surf

    def draw(self, lines, screen_size, corner='tl'):
        key = (tuple(lines), corner)
        if key != self._key:
            surf = self._surface(lines)
            data = pygame.image.tostring(surf, 'RGBA', False)
            size = surf.get_size()
            if self.tex is None or self._size != size:
                if self.tex is not None:
                    self.tex.release()
                self.tex = self.ctx.texture(size, 4, data)
                self.tex.filter = (moderngl.NEAREST, moderngl.NEAREST)
                self._size = size
            else:
                self.tex.write(data)
            self._key = key

        sw, sh = screen_size
        w, h = self._size
        m = 12
        x0 = m if 'l' in corner else sw - w - m
        y0 = m if 't' in corner else sh - h - m
        x1, y1 = x0 + w, y0 + h

        def ndc(x, y):
            return (2.0 * x / sw - 1.0, 1.0 - 2.0 * y / sh)

        a, b = ndc(x0, y0), ndc(x1, y1)
        quad = np.array([
            a[0], a[1], 0.0, 0.0,
            b[0], a[1], 1.0, 0.0,
            b[0], b[1], 1.0, 1.0,
            a[0], a[1], 0.0, 0.0,
            b[0], b[1], 1.0, 1.0,
            a[0], b[1], 0.0, 1.0,
        ], dtype='f4')
        self.vbo.write(quad.tobytes())

        self.ctx.disable(moderngl.DEPTH_TEST | moderngl.CULL_FACE)
        self.ctx.enable(moderngl.BLEND)
        self.ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA
        self.tex.use(1)
        self.prog['u_tex'].value = 1
        self.vao.render(moderngl.TRIANGLES, vertices=6)
        self.ctx.disable(moderngl.BLEND)
        self.ctx.enable(moderngl.DEPTH_TEST)
