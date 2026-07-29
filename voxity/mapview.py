"""Pick the square to play on, off the baked overview map.

Runs its own little event loop against an already-open pygame/moderngl window
and hands back a lon/lat box, or None if the player quit. Everything here works
in *top-down uv*: (0,0) is the north-west corner of the map, (1,1) the
south-east one. Metres come from the OverviewMap's projection, so the square
stays square on the ground rather than in pixels.
"""

import moderngl
import numpy as np
import pygame

from . import shaders
from .geo import square_bbox

MIN_SIZE = 200.0
MAX_SIZE = 8000.0
SIZE_STEP = 1.18
ZOOM_STEP = 1.25
MAX_ZOOM = 60.0

HELP = [
    ('move mouse   place the square', True),
    ('wheel   square size', True),
    ('ctrl+wheel or + -   zoom map', True),
    ('right-drag or arrows   pan', True),
    ('click or ENTER   play here', True),
    ('ESC   quit', True),
]


class MapView:
    """Draws the baked map with a selection square and drives the picking."""

    def __init__(self, ctx, omap, size_m=1200.0):
        self.ctx = ctx
        self.omap = omap
        self.size_m = float(size_m)
        self.zoom = 1.0
        self.centre = [0.5, 0.5]          # uv, top-down
        self.cursor = [0.5, 0.5]          # uv, top-down

        self.tex = ctx.texture(omap.size, 3, omap.pixels)
        self.tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self.tex.repeat_x = False
        self.tex.repeat_y = False

        self.prog = ctx.program(vertex_shader=shaders.MAPVIEW_VS,
                                fragment_shader=shaders.MAPVIEW_FS)
        quad = np.array([0, 0, 1, 0, 1, 1,
                         0, 0, 1, 1, 0, 1], dtype='f4')
        self.vao = ctx.vertex_array(
            self.prog, [(ctx.buffer(quad.tobytes()), '2f', 'in_pos')])

    # -- view transform ------------------------------------------------------

    def span(self, screen):
        """Visible uv extent, chosen so the map keeps its aspect ratio."""
        mw, mh = self.omap.size
        win = screen[0] / max(screen[1], 1)
        if win > mw / mh:                 # window wider than the map: fit height
            sv = 1.0
            su = win * mh / mw
        else:
            su = 1.0
            sv = (mw / mh) / win
        return su / self.zoom, sv / self.zoom

    def view_uniform(self, screen):
        su, sv = self.span(screen)
        return (su, sv, self.centre[0] - su * 0.5, self.centre[1] - sv * 0.5)

    def screen_to_uv(self, pos, screen):
        su, sv, ou, ov = self.view_uniform(screen)
        return [ou + (pos[0] / max(screen[0], 1)) * su,
                ov + (pos[1] / max(screen[1], 1)) * sv]

    def selection_uv(self):
        """The square in uv, as (x0, y0, x1, y1)."""
        minx, minz, maxx, maxz = self.omap.extent
        hu = 0.5 * self.size_m / (maxx - minx)
        hv = 0.5 * self.size_m / (maxz - minz)
        u, v = self.cursor
        return (u - hu, v - hv, u + hu, v + hv)

    def lonlat(self):
        return self.omap.uv_to_lonlat(self.cursor[0], self.cursor[1])

    # -- interaction ---------------------------------------------------------

    def zoom_at(self, factor, pos, screen):
        """Zoom keeping the map point under `pos` where it is."""
        before = self.screen_to_uv(pos, screen)
        self.zoom = float(np.clip(self.zoom * factor, 1.0, MAX_ZOOM))
        after = self.screen_to_uv(pos, screen)
        self.centre[0] += before[0] - after[0]
        self.centre[1] += before[1] - after[1]
        self.clamp()

    def clamp(self):
        self.centre[0] = float(np.clip(self.centre[0], -0.25, 1.25))
        self.centre[1] = float(np.clip(self.centre[1], -0.25, 1.25))

    def draw(self, screen):
        self.ctx.disable(moderngl.DEPTH_TEST | moderngl.CULL_FACE)
        su, sv, ou, ov = self.view_uniform(screen)
        self.tex.use(0)
        self.prog['u_tex'].value = 0
        self.prog['u_view'].value = (su, sv, ou, ov)
        self.prog['u_sel'].value = self.selection_uv()
        # one-pixel border, expressed in uv
        self.prog['u_border'].value = (su / max(screen[0], 1),
                                       sv / max(screen[1], 1))
        self.prog['u_edge_col'].value = (0.97, 0.32, 0.24)
        self.vao.render(moderngl.TRIANGLES, vertices=6)

    def release(self):
        self.tex.release()
        self.vao.release()
        self.prog.release()


def choose_region(ctx, omap, screen_size, overlay, help_overlay,
                  size_m=1200.0, clock=None, frames=0):
    """Run the picker. Returns (bbox_ll, size_m) or None if the player quit.

    `frames` quits after that many frames without a choice — for smoke tests.
    """
    view = MapView(ctx, omap, size_m)
    clock = clock or pygame.time.Clock()
    panning = False
    frame = 0
    result = None

    pygame.mouse.set_visible(True)
    pygame.event.set_grab(False)
    running = True
    while running:
        dt = min(clock.tick(120) / 1000.0, 0.1)
        frame += 1
        if frames and frame > frames:
            break
        size = pygame.display.get_window_size()
        mods = pygame.key.get_mods()

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.VIDEORESIZE:
                size = (max(320, event.w), max(240, event.h))
                ctx.viewport = (0, 0, *size)
            elif event.type == pygame.MOUSEMOTION:
                if panning:
                    su, sv = view.span(size)
                    view.centre[0] -= event.rel[0] / max(size[0], 1) * su
                    view.centre[1] -= event.rel[1] / max(size[1], 1) * sv
                    view.clamp()
                view.cursor = view.screen_to_uv(event.pos, size)
            elif event.type == pygame.MOUSEBUTTONDOWN:
                if event.button == 1:
                    view.cursor = view.screen_to_uv(event.pos, size)
                    result = view.lonlat()
                    running = False
                elif event.button in (2, 3):
                    panning = True
                elif event.button in (4, 5):
                    up = event.button == 4
                    if mods & pygame.KMOD_CTRL:
                        view.zoom_at(ZOOM_STEP if up else 1 / ZOOM_STEP,
                                     event.pos, size)
                    else:
                        step = SIZE_STEP if up else 1 / SIZE_STEP
                        view.size_m = float(np.clip(view.size_m * step,
                                                    MIN_SIZE, MAX_SIZE))
            elif event.type == pygame.MOUSEBUTTONUP and event.button in (2, 3):
                panning = False
            elif event.type == pygame.KEYDOWN:
                k = event.key
                if k == pygame.K_ESCAPE:
                    running = False
                elif k in (pygame.K_RETURN, pygame.K_KP_ENTER):
                    result = view.lonlat()
                    running = False
                elif k in (pygame.K_PLUS, pygame.K_EQUALS, pygame.K_KP_PLUS):
                    view.zoom_at(ZOOM_STEP, (size[0] // 2, size[1] // 2), size)
                elif k in (pygame.K_MINUS, pygame.K_KP_MINUS):
                    view.zoom_at(1 / ZOOM_STEP, (size[0] // 2, size[1] // 2),
                                 size)

        keys = pygame.key.get_pressed()
        pan = 0.55 * dt / view.zoom
        if keys[pygame.K_LEFT]:
            view.centre[0] -= pan
        if keys[pygame.K_RIGHT]:
            view.centre[0] += pan
        if keys[pygame.K_UP]:
            view.centre[1] -= pan
        if keys[pygame.K_DOWN]:
            view.centre[1] += pan
        view.clamp()

        ctx.screen.use()
        ctx.viewport = (0, 0, *size)
        ctx.screen.clear(0.06, 0.07, 0.08, 1.0, depth=1.0)
        view.draw(size)

        lon, lat = view.lonlat()
        m_px = omap.metres_per_pixel
        info = [
            ('voxity — pick a region', False),
            (f'{lat:.5f}, {lon:.5f}', False),
            (f'{view.size_m:.0f} m square   zoom {view.zoom:.1f}x'
             f'   {m_px:.0f} m/px', True),
        ]
        overlay.draw(info, size, 'tl')
        help_overlay.draw(HELP, size, 'bl')
        pygame.display.flip()

    view.release()
    if result is None:
        return None
    lon, lat = result
    return square_bbox(lon, lat, view.size_m), view.size_m
