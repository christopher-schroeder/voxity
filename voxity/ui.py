"""Flat 2D widgets over the GL window: rectangles, outlines, text, menus.

The editor's chrome used to be immediate-mode fixed-function calls, which the
core 3.3 context the city runs in does not have. This is the replacement, and
it is deliberately generic — the start screen uses it too.

Everything is in **pixels with y growing downward**, which is what pygame
reports for the mouse, so a widget's hit test is its draw rectangle. Solid
shapes batch into one buffer and keep insertion order (later covers earlier, so
a dropdown drawn last lands on top); text is drawn afterwards, over all of it,
from cached textures.

`hud.Overlay` is the other text path and stays separate: it owns a whole
bordered panel and re-renders whenever the lines change, which is right for a
live FPS counter and wrong for the fixed labels here.
"""

import moderngl
import numpy as np
import pygame

from . import shaders

MAX_QUADS = 4096


def _rgba(col):
    return (col[0], col[1], col[2], col[3] if len(col) > 3 else 1.0)


class UI:
    def __init__(self, ctx, font_size=16):
        self.ctx = ctx
        pygame.font.init()
        # two sizes is all anything here needs: labels, and the odd heading
        self.font = pygame.font.SysFont('dejavusansmono,consolas,monospace',
                                        font_size)
        self.big_font = pygame.font.SysFont('dejavusansmono,consolas,monospace',
                                            round(font_size * 2.4))
        self.prog = ctx.program(vertex_shader=shaders.UI_VS,
                                fragment_shader=shaders.UI_FS)
        self.vbo = ctx.buffer(reserve=MAX_QUADS * 6 * 6 * 4, dynamic=True)
        self.vao = ctx.vertex_array(
            self.prog, [(self.vbo, '2f 4f', 'in_pos', 'in_col')])

        self.text_prog = ctx.program(vertex_shader=shaders.OVERLAY_VS,
                                     fragment_shader=shaders.OVERLAY_FS)
        self.text_vbo = ctx.buffer(reserve=6 * 4 * 4, dynamic=True)
        self.text_vao = ctx.vertex_array(
            self.text_prog, [(self.text_vbo, '2f 2f', 'in_pos', 'in_uv')])

        self._cache = {}                 # (text, colour) -> (texture, w, h)
        self._quads = []
        self._labels = []
        self.size = (1, 1)

    # -- frame ---------------------------------------------------------------

    def begin(self, size):
        self.size = size
        self._quads.clear()
        self._labels.clear()

    def rect(self, x, y, w, h, col):
        r, g, b, a = _rgba(col)
        x0, y0, x1, y1 = x, y, x + w, y + h
        self._quads.append([
            x0, y0, r, g, b, a, x1, y0, r, g, b, a, x1, y1, r, g, b, a,
            x0, y0, r, g, b, a, x1, y1, r, g, b, a, x0, y1, r, g, b, a,
        ])

    def outline(self, x, y, w, h, col, px=2):
        self.rect(x - px, y - px, w + 2 * px, px, col)
        self.rect(x - px, y + h, w + 2 * px, px, col)
        self.rect(x - px, y, px, h, col)
        self.rect(x + w, y, px, h, col)

    def measure(self, text, big=False):
        return (self.big_font if big else self.font).size(text)

    def text(self, text, x, y, col=(0.92, 0.93, 0.95), big=False):
        """Queue a label; returns its (w, h) so callers can lay out around it."""
        tex, w, h = self._texture(text, col, big)
        self._labels.append((tex, x, y, w, h))
        return w, h

    def text_centred(self, text, cx, cy, col=(0.92, 0.93, 0.95), big=False):
        w, h = self.measure(text, big)
        return self.text(text, cx - w // 2, cy - h // 2, col, big)

    def _texture(self, text, col, big=False):
        key = (text, tuple(round(c, 3) for c in col[:3]), big)
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        rgb = tuple(round(255 * c) for c in col[:3])
        font = self.big_font if big else self.font
        surf = font.render(text, True, rgb).convert_alpha()
        w, h = surf.get_size()
        # unflipped: texture row 0 is the surface's top row, which is what the
        # v=0-at-top quad below expects (same convention as hud.Overlay)
        tex = self.ctx.texture((w, h), 4, pygame.image.tostring(surf, 'RGBA', False))
        tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self._cache[key] = (tex, w, h)
        return self._cache[key]

    def flush(self):
        """Draw everything queued this frame: solids first, then labels."""
        ctx = self.ctx
        ctx.disable(moderngl.DEPTH_TEST | moderngl.CULL_FACE)
        ctx.enable(moderngl.BLEND)
        ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA

        if self._quads:
            data = np.array(self._quads, dtype='f4').reshape(-1, 6)
            n = min(len(data), MAX_QUADS * 6)
            self.vbo.write(data[:n].tobytes())
            self.prog['u_screen'].value = (float(self.size[0]), float(self.size[1]))
            self.vao.render(moderngl.TRIANGLES, vertices=n)

        sw, sh = self.size
        for tex, x, y, w, h in self._labels:
            x0, y0 = 2.0 * x / sw - 1.0, 1.0 - 2.0 * y / sh
            x1, y1 = 2.0 * (x + w) / sw - 1.0, 1.0 - 2.0 * (y + h) / sh
            quad = np.array([x0, y0, 0, 0, x1, y0, 1, 0, x1, y1, 1, 1,
                             x0, y0, 0, 0, x1, y1, 1, 1, x0, y1, 0, 1],
                            dtype='f4')
            self.text_vbo.write(quad.tobytes())
            tex.use(2)
            self.text_prog['u_tex'].value = 2
            self.text_vao.render(moderngl.TRIANGLES, vertices=6)

        ctx.disable(moderngl.BLEND)
        ctx.enable(moderngl.DEPTH_TEST)
        self._quads.clear()
        self._labels.clear()

    def release(self):
        for tex, _, _ in self._cache.values():
            tex.release()
        self._cache.clear()
        for obj in (self.vao, self.vbo, self.prog,
                    self.text_vao, self.text_vbo, self.text_prog):
            obj.release()


# --- menubar ----------------------------------------------------------------

MENUBAR_H = 24
_MENU_PAD = 12
_ITEM_H = 24
_ITEM_PAD = 14

BAR_BG = (0.16, 0.17, 0.20)
BAR_OPEN = (0.28, 0.30, 0.36)
DROP_BG = (0.20, 0.21, 0.25)
DROP_HI = (0.30, 0.40, 0.55)
DROP_EDGE = (0.35, 0.37, 0.42)


class Menubar:
    """A File/View/Help bar. `defs` is [(title, [(label, action), ...]), ...]."""

    def __init__(self, ui, defs):
        self.ui = ui
        self.open = None
        self.titles = []
        x = 4
        for label, items in defs:
            tw, _ = ui.measure(label)
            iw = max((ui.measure(lbl)[0] for lbl, _ in items), default=0)
            w = tw + _MENU_PAD * 2
            self.titles.append({'label': label, 'x': x, 'w': w, 'items': items,
                                'menu_w': max(w, iw + _ITEM_PAD * 2)})
            x += w

    def _title_at(self, mx, my):
        if my > MENUBAR_H:
            return None
        for i, t in enumerate(self.titles):
            if t['x'] <= mx < t['x'] + t['w']:
                return i
        return None

    def _item_at(self, mx, my):
        if self.open is None:
            return None
        t = self.titles[self.open]
        if not (t['x'] <= mx < t['x'] + t['menu_w'] and my >= MENUBAR_H):
            return None
        idx = int((my - MENUBAR_H) // _ITEM_H)
        return idx if 0 <= idx < len(t['items']) else None

    def handle_click(self, mx, my):
        """Process a left-click. Returns (consumed, action_or_None)."""
        ti = self._title_at(mx, my)
        if ti is not None:
            self.open = None if self.open == ti else ti
            return True, None
        if self.open is not None:                  # a click while a menu is open
            ii = self._item_at(mx, my)
            act = self.titles[self.open]['items'][ii][1] if ii is not None else None
            self.open = None
            return True, act                       # swallow the click either way
        return False, None

    def draw(self, w, mouse):
        ui = self.ui
        ui.rect(0, 0, w, MENUBAR_H, BAR_BG)
        for i, t in enumerate(self.titles):
            if i == self.open:
                ui.rect(t['x'], 0, t['w'], MENUBAR_H, BAR_OPEN)
            th = ui.measure(t['label'])[1]
            ui.text(t['label'], t['x'] + _MENU_PAD, (MENUBAR_H - th) // 2)
        if self.open is None:
            return
        t = self.titles[self.open]
        x0, mw, n = t['x'], t['menu_w'], len(t['items'])
        ui.rect(x0, MENUBAR_H, mw, n * _ITEM_H, DROP_BG)
        hi = self._item_at(*mouse)
        if hi is not None:
            ui.rect(x0, MENUBAR_H + hi * _ITEM_H, mw, _ITEM_H, DROP_HI)
        ui.outline(x0, MENUBAR_H, mw, n * _ITEM_H, DROP_EDGE, px=1)
        for j, (label, _) in enumerate(t['items']):
            ih = ui.measure(label)[1]
            ui.text(label, x0 + _ITEM_PAD,
                    MENUBAR_H + j * _ITEM_H + (_ITEM_H - ih) // 2)
