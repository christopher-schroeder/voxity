"""Pick the square to play on, off the baked overview map.

Runs its own little event loop against an already-open pygame/moderngl window
and hands back a lon/lat box, or None if the player quit. Everything here works
in *top-down uv*: (0,0) is the north-west corner of the map, (1,1) the
south-east one.

The map is divided into a fixed grid of squares and you pick one of them — the
cell size is set once from `--size` and never changes while picking, so every
region is the same size and two regions either are the same or don't overlap at
all. The grid is laid out in the map's *metres*, not in uv, so cells come out
square on the ground.
"""

import moderngl
import numpy as np
import pygame

from . import shaders
from .geo import square_bbox

MIN_SIZE = 200.0
MAX_SIZE = 8000.0
ZOOM_STEP = 1.25
MAX_ZOOM = 60.0

# grid lines fade out once cells get too small to aim at
GRID_FADE_PX = (7.0, 16.0)
GRID_ALPHA = 0.3

HELP = [
    ('move mouse   pick a cell', True),
    ('arrows   next cell', True),
    ('wheel or + -   zoom map', True),
    ('right-drag   pan', True),
    ('click or ENTER   play here', True),
    ('ESC   quit', True),
]


class MapView:
    """Draws the baked map with a grid of regions and drives the picking."""

    def __init__(self, ctx, omap, size_m=1200.0):
        self.ctx = ctx
        self.omap = omap
        self.size_m = float(np.clip(size_m, MIN_SIZE, MAX_SIZE))
        self.zoom = 1.0
        self.centre = [0.5, 0.5]          # uv, top-down

        # The grid is centred on the map rather than anchored at a corner, so
        # the part that doesn't divide evenly is split between both edges
        # instead of piling up on one. Cells keep their exact metre size.
        minx, minz, maxx, maxz = omap.extent
        self.cell = (self.size_m / (maxx - minx), self.size_m / (maxz - minz))
        self.count = (max(1, round(1.0 / self.cell[0])),
                      max(1, round(1.0 / self.cell[1])))
        self.origin = (0.5 - 0.5 * self.count[0] * self.cell[0],
                       0.5 - 0.5 * self.count[1] * self.cell[1])
        self.sel = [self.count[0] // 2, self.count[1] // 2]

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

    # -- the grid ------------------------------------------------------------

    def select_at(self, uv):
        """Select the cell containing a uv point, if there is one."""
        i = int(np.floor((uv[0] - self.origin[0]) / self.cell[0]))
        j = int(np.floor((uv[1] - self.origin[1]) / self.cell[1]))
        if 0 <= i < self.count[0] and 0 <= j < self.count[1]:
            self.sel = [i, j]

    def move_sel(self, di, dj):
        self.sel[0] = int(np.clip(self.sel[0] + di, 0, self.count[0] - 1))
        self.sel[1] = int(np.clip(self.sel[1] + dj, 0, self.count[1] - 1))

    def selection_uv(self):
        """The selected cell in uv, as (x0, y0, x1, y1)."""
        x0 = self.origin[0] + self.sel[0] * self.cell[0]
        y0 = self.origin[1] + self.sel[1] * self.cell[1]
        return (x0, y0, x0 + self.cell[0], y0 + self.cell[1])

    def lonlat(self):
        """Centre of the selected cell."""
        x0, y0, x1, y1 = self.selection_uv()
        return self.omap.uv_to_lonlat(0.5 * (x0 + x1), 0.5 * (y0 + y1))

    def ensure_visible(self, screen):
        """Pan the least amount that brings the selected cell back on screen."""
        su, sv = self.span(screen)
        x0, y0, x1, y1 = self.selection_uv()
        for axis, (lo, hi, s) in enumerate(((x0, x1, su), (y0, y1, sv))):
            margin = min(s * 0.08, 0.5 * (s - (hi - lo)))
            if margin < 0:                # cell bigger than the view: centre it
                self.centre[axis] = 0.5 * (lo + hi)
                continue
            view_lo = self.centre[axis] - s * 0.5
            view_hi = self.centre[axis] + s * 0.5
            if lo - margin < view_lo:
                self.centre[axis] -= view_lo - (lo - margin)
            elif hi + margin > view_hi:
                self.centre[axis] += (hi + margin) - view_hi
        self.clamp()

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
        border = (su / max(screen[0], 1), sv / max(screen[1], 1))
        lo, hi = GRID_FADE_PX
        cell_px = self.cell[0] / su * screen[0]
        fade = float(np.clip((cell_px - lo) / (hi - lo), 0.0, 1.0))

        self.tex.use(0)
        self.prog['u_tex'].value = 0
        self.prog['u_view'].value = (su, sv, ou, ov)
        self.prog['u_sel'].value = self.selection_uv()
        self.prog['u_border'].value = border
        self.prog['u_edge_col'].value = (0.97, 0.32, 0.24)
        self.prog['u_grid'].value = (*self.cell, *self.origin)
        self.prog['u_grid_n'].value = self.count
        self.prog['u_grid_alpha'].value = fade * GRID_ALPHA
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
        clock.tick(120)
        frame += 1
        if frames and frame > frames:
            break
        size = pygame.display.get_window_size()

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
                else:
                    view.select_at(view.screen_to_uv(event.pos, size))
            elif event.type == pygame.MOUSEBUTTONDOWN:
                if event.button == 1:
                    view.select_at(view.screen_to_uv(event.pos, size))
                    result = view.lonlat()
                    running = False
                elif event.button in (2, 3):
                    panning = True
                elif event.button in (4, 5):
                    view.zoom_at(ZOOM_STEP if event.button == 4
                                 else 1 / ZOOM_STEP, event.pos, size)
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
                elif k in (pygame.K_LEFT, pygame.K_RIGHT,
                           pygame.K_UP, pygame.K_DOWN):
                    # one cell per press, not per frame — the grid is discrete
                    view.move_sel((k == pygame.K_RIGHT) - (k == pygame.K_LEFT),
                                  (k == pygame.K_DOWN) - (k == pygame.K_UP))
                    view.ensure_visible(size)

        ctx.screen.use()
        ctx.viewport = (0, 0, *size)
        ctx.screen.clear(0.06, 0.07, 0.08, 1.0, depth=1.0)
        view.draw(size)

        lon, lat = view.lonlat()
        info = [
            ('voxity — pick a region', False),
            (f'{lat:.5f}, {lon:.5f}', False),
            (f'cell {view.sel[0] + 1},{view.sel[1] + 1}'
             f' of {view.count[0]}x{view.count[1]}'
             f'   {view.size_m:.0f} m   zoom {view.zoom:.1f}x', True),
        ]
        overlay.draw(info, size, 'tl')
        help_overlay.draw(HELP, size, 'bl')
        pygame.display.flip()

    view.release()
    if result is None:
        return None
    lon, lat = result
    return square_bbox(lon, lat, view.size_m), view.size_m
