"""The voxel editor: build the models the city is made of.

Runs against the window and moderngl context main.py already opened, so the
start screen can hand control here and take it back. Returns 'quit' when the
window was closed and 'menu' on ESC.
"""

import os

import numpy as np
import pygame

from .. import footprints
from .. import ui as uikit
from .. import voxel
from ..build import GROUND_COLOUR
from ..camera import OrbitCamera, screen_ray, to_gl
from ..mesh import MAT_MATTE, MeshBuilder
from . import io
from .browse import ask_path
from .choose import choose_footprint
from .edits import History, changes
from .pick import (DEFAULT_BRUSH, brush_block, erase_block, pick,
                   resize_brush)
from .render import EditorRenderer, box_lines

DEFAULT_MODEL = voxel.DEFAULT_MODEL

DRAG_THRESHOLD = 5       # pixels; below this a press+release counts as a click

SWATCH = 30
SWATCH_GAP = 3
SWATCH_MARGIN = 14
SWATCH_TOP = uikit.MENUBAR_H + SWATCH_MARGIN
ROW_GAP = 8

# One stepper row per brush axis, below the swatches: 'X [-] 4 [+]'.
BRUSH_TOP = SWATCH_TOP + SWATCH + ROW_GAP
BRUSH_AXES = 'XYZ'
BRUSH_ROW_H = 22
BRUSH_BTN = 18
BRUSH_LBL_W = 14
BRUSH_VAL_W = 26
BRUSH_BTN_COL = (0.26, 0.28, 0.33)
BRUSH_LBL_COL = (0.62, 0.66, 0.72)

# What the model currently costs, under the brush steppers.
STATS_TOP = BRUSH_TOP + 3 * BRUSH_ROW_H + ROW_GAP
STATS_LINE_H = 17
STATS_COL = (0.62, 0.66, 0.72)
STATS_KEY_COL = (0.82, 0.86, 0.90)
# A dark backing for that column. The labels are a muted grey chosen against
# the editor's near-black background, and the city-lighting preview puts a
# sunlit ground behind them, where they vanish.
PANEL_COL = (0.08, 0.09, 0.11, 0.62)

# The city-lighting preview stands the model on a patch of ground this many
# metres across. There has to be one: half of what the city's look does to a
# building is the contact shadow and the occlusion where it meets the ground,
# and a model floating in the sky shows neither.
CITY_GROUND_M = 40.0

HOVER_COL = (0.95, 0.30, 0.30)      # the voxel a right-click would delete
PLACE_COL = (0.30, 0.95, 0.40)      # where a left-click would add
BLOCKED_COL = (0.55, 0.55, 0.58)    # ... and where it would do nothing

MENU_DEFS = [
    ('File', [('New', 'new'), ('Open...', 'open'), ('Save', 'save'),
              ('Save As...', 'save_as'), ('Export OBJ...', 'export_obj'),
              ('Export PNG...', 'export_png'), ('Back to menu', 'menu')]),
    ('View', [('Toggle Grid', 'toggle_grid'), ('Show Triangles', 'toggle_wire'),
              ('City Lighting', 'toggle_city'),
              ('Reset Camera', 'reset_cam'), ('Frame Model', 'frame'),
              ('Reset Brush', 'reset_brush')]),
    ('Edit', [('Undo', 'undo'), ('Redo', 'redo'),
              ('Pick Hue Under Cursor', 'eyedrop'),
              ('Paint Brush Area', 'paint'),
              ('Fill Region', 'fill_region'),
              ('Replace This Hue', 'replace_hue'),
              ('Fill Enclosed Holes', 'fill_holes')]),
    ('Ground', [('Choose Footprint...', 'fp_choose'),
                ('Open Footprint File...', 'fp_open'),
                ('Clear Footprint', 'fp_clear')]),
]

# x/y/z resize one brush axis; shift shrinks instead of growing.
BRUSH_KEYS = {pygame.K_x: 0, pygame.K_y: 1, pygame.K_z: 2}

HELP = [
    ('L-drag orbit   R-drag pan   wheel zoom', True),
    ('L-click add   R-click delete brush area', True),
    ('shift L-click  paint   M-click  pick hue', True),
    ('ctrl-Z undo   ctrl-Y redo', True),
    ('H fill holes   K fill region   R replace hue', True),
    ('swatch or 1-0   hue', True),
    ('X Y Z  grow brush axis   +shift  shrink', True),
    (', .  shrink / grow all three    G  grid', True),
    ('T  show the triangles over the model', True),
    ('C  light it the way the city will', True),
    ('S / L  save / load    F  frame model', True),
    ('B  choose a footprint to build on', True),
    ('ESC  back to the menu', True),
]


def swatch_rect(i):
    return (SWATCH_MARGIN + i * (SWATCH + SWATCH_GAP), SWATCH_TOP, SWATCH, SWATCH)


def swatch_at(mx, my):
    """Index of the hue swatch under the cursor, or None."""
    for i in range(voxel.N_HUES):
        x, y, w, h = swatch_rect(i)
        if x <= mx <= x + w and y <= my <= y + h:
            return i
    return None


def brush_btn_rect(axis, delta):
    """Rect of one brush stepper button; `delta` is -1 for '-' and +1 for '+'."""
    x = SWATCH_MARGIN + BRUSH_LBL_W
    if delta > 0:
        x += BRUSH_BTN + BRUSH_VAL_W
    return (x, BRUSH_TOP + axis * BRUSH_ROW_H, BRUSH_BTN, BRUSH_BTN)


def brush_btn_at(mx, my):
    """(axis, delta) of the brush stepper under the cursor, or None."""
    for axis in range(3):
        for delta in (-1, 1):
            x, y, w, h = brush_btn_rect(axis, delta)
            if x <= mx <= x + w and y <= my <= y + h:
                return axis, delta
    return None


def model_stats(voxels, n_verts):
    """Lines for the stats panel: what the model costs, and how big it is.

    Vertices *and* triangles, because between them they show the greedy mesher
    working: a flat wall of one hue is two triangles however many voxels it is,
    so a voxel count that climbs while the triangle count does not is merging
    doing its job. `height` is the model's own y extent, not its top — a model
    built above the grid is as tall as it looks.
    """
    b = voxel.bounds(voxels)
    lo, hi = b if b is not None else ((0, 0, 0), (0, 0, 0))
    span = tuple(hi[i] - lo[i] for i in range(3))
    return [f'{n_verts // 3:,} tris   {n_verts:,} verts',
            f'height {span[1]}   {span[0]}x{span[2]} base',
            f'{len(voxels):,} voxels']


def ground_verts(half_m, cell=voxel.CELL_M):
    """A square of city ground under the preview, in **metres**."""
    mb = MeshBuilder()
    quad = np.array([[-half_m, 0.0, -half_m], [half_m, 0.0, -half_m],
                     [half_m, 0.0, half_m], [-half_m, 0.0, -half_m],
                     [half_m, 0.0, half_m], [-half_m, 0.0, half_m]],
                    dtype=np.float32)
    mb.add(quad, np.array([0.0, 1.0, 0.0], dtype=np.float32),
           np.array(GROUND_COLOUR, dtype=np.float32), MAT_MATTE)
    return mb.pack()


def seed_model():
    """Something on screen on a cold start, so the grid isn't just empty."""
    voxels = {}
    for x in range(-1, 2):
        for z in range(-1, 2):
            voxels[(x, 0, z)] = 20            # hue only; the value is positional
    voxels[(0, 1, 0)] = 0                     # red-ish
    voxels[(1, 1, 1)] = 11                    # green-ish
    voxels[(-1, 1, -1)] = 22                  # blue-ish
    return voxels


def _footprint_of(path):
    """The ground a saved model recorded, or None. Never raises."""
    try:
        return voxel.load_footprint(path)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def load_or_seed(path):
    """The model at `path`, or the seed scene when there isn't a usable one.

    An empty file counts as "nothing there": opening onto a bare grid gives
    you nothing to orbit around and no clue that the editor works.
    """
    try:
        voxels = voxel.load(path)
    except (OSError, ValueError, KeyError):
        return seed_model()
    if not voxels:
        return seed_model()
    print(f'loaded {len(voxels)} voxels from {path}')
    return voxels


class Editor:
    """Editor state and the per-frame work, split out so main can drive it."""

    def __init__(self, ctx, model_path=DEFAULT_MODEL):
        self.ctx = ctx
        self.renderer = EditorRenderer(ctx)
        self.voxels = load_or_seed(model_path)
        self.path = model_path
        self.cam = OrbitCamera(target=voxel.centre(self.voxels))
        self.hue = voxel.N_HUES // 2
        self.brush = DEFAULT_BRUSH
        self.show_grid = True
        self.show_wire = False                # triangle overlay, off by default
        self.city_light = False               # preview through the city renderer
        self.city = None                      # its Renderer, built on first use
        self.city_stale = True
        self.dirty = True
        self.hover = None                     # cell under the cursor
        self.block = None                     # min corner of the block to place
        self.footprint = None                 # {(x, z)} the building may occupy
        self.history = History()
        self.stats = model_stats(self.voxels, 0)
        self.set_footprint(_footprint_of(model_path))
        self.frame()                          # open looking at whatever loaded

    def set_footprint(self, cells):
        """Constrain building to `cells`, or lift the constraint with None.

        Voxels already outside are left alone rather than deleted — the ground
        is a guide for what you build next, and silently eating work because a
        plan was swapped would be the wrong trade.
        """
        self.footprint = set(cells) if cells else None
        self.renderer.set_footprint(self.footprint)
        if self.footprint:
            stray = sum(1 for x, _, z in self.voxels if (x, z) not in self.footprint)
            if stray:
                print(f'note: {stray} voxel(s) sit outside the footprint')

    def allowed(self, cells):
        """The cells of `cells` the footprint permits."""
        if self.footprint is None:
            return list(cells)
        return [c for c in cells if (c[0], c[2]) in self.footprint]

    def remesh(self):
        verts = voxel.model_vertices(self.voxels)
        self.renderer.upload(verts)
        # only place the counts can be right: they are what the mesher just
        # produced, not an estimate of what it would produce
        self.stats = model_stats(self.voxels, len(verts))
        self.dirty = False
        self.city_stale = True
        return verts

    def aim(self, mouse, size):
        """Update hover/placement targets from the mouse, using last frame's camera."""
        vp = self.cam.projection(size[0] / max(size[1], 1)) @ self.cam.view()
        origin, direction = screen_ray(np.linalg.inv(vp), mouse, size)
        self.hover, info = pick(self.voxels, origin, direction)
        self.block = brush_block(self.hover, info, self.brush)

    # --- editing -----------------------------------------------------------
    #
    # Everything that changes the model goes through `apply`, so undo gets its
    # diff without each command having to remember to record one.

    def apply(self, wanted):
        """Set or clear cells, recording the step. Returns how many changed."""
        diff = changes(self.voxels, wanted)
        if not diff:
            return 0
        before = {c: self.voxels.get(c) for c in diff}
        for cell, hue in diff.items():
            if hue is None:
                self.voxels.pop(cell, None)
            else:
                self.voxels[cell] = hue
        self.history.push(before)
        self.dirty = True
        return len(diff)

    def set_model(self, voxels):
        """Replace the whole model, undoably."""
        wanted = dict.fromkeys(self.voxels)
        wanted.update(voxels)
        self.apply(wanted)

    def undo(self):
        n = self.history.undo(self.voxels)
        self.dirty = self.dirty or bool(n)
        print(f'undo: {n} cells' if n else 'nothing to undo')

    def redo(self):
        n = self.history.redo(self.voxels)
        self.dirty = self.dirty or bool(n)
        print(f'redo: {n} cells' if n else 'nothing to redo')

    def erase_area(self):
        """The brush-sized block centred on the hovered voxel, or None."""
        return erase_block(self.hover, self.brush)

    def add(self):
        if self.block is None:
            return
        cells = self.allowed(voxel.block_cells(self.block, self.brush))
        self.apply(dict.fromkeys(cells, self.hue))

    def delete(self):
        """Remove the brush-sized block under the cursor, not one voxel.

        The brush is 1x1x1 by default, so this is the old behaviour until you
        make the brush bigger — which is the point: one control sizes both what
        you place and what you take away.
        """
        mn = self.erase_area()
        if mn is None:
            return
        self.apply({c: None for c in voxel.block_cells(mn, self.brush)
                    if c in self.voxels})

    def paint(self):
        """Recolour what is already there, without adding or removing anything."""
        mn = self.erase_area()
        if mn is None:
            return
        n = self.apply({c: self.hue for c in voxel.block_cells(mn, self.brush)
                        if c in self.voxels})
        if not n:
            print('nothing under the brush to paint')

    def eyedrop(self):
        """Take the hue of the voxel under the cursor."""
        hue = self.voxels.get(self.hover)
        if hue is None:
            print('no voxel under the cursor')
            return
        self.hue = int(hue)
        print(f'hue {self.hue}')

    def fill_region(self):
        """Flood the connected same-hue run under the cursor with the current hue."""
        if self.hover is None:
            print('point at a voxel first')
            return
        cells = voxel.region(self.voxels, self.hover)
        n = self.apply(dict.fromkeys(cells, self.hue))
        print(f'filled {n} of {len(cells)} cells' if cells
              else 'nothing to fill there')

    def replace_hue(self):
        """Every voxel of the hovered hue takes the current one."""
        old = self.voxels.get(self.hover)
        if old is None:
            print('point at a voxel first')
            return
        n = self.apply({c: self.hue for c, h in self.voxels.items() if h == old})
        print(f'replaced hue {old} with {self.hue} on {n} cells')

    def fill_holes(self):
        """Fill the sealed cavities inside the model with the current hue."""
        holes = voxel.cavities(self.voxels)
        n = self.apply(dict.fromkeys(holes, self.hue))
        print(f'filled {n} enclosed cells' if n else 'no enclosed holes')

    def frame(self):
        """Point the camera at the model and back off far enough to see it.

        The footprint counts as part of what to look at: opening a bare plan
        would otherwise frame an empty model and leave the ground off screen.
        """
        seen = dict(self.voxels)
        if self.footprint:
            seen.update({(x, 0, z): 0 for x, z in self.footprint})
        self.cam.target = voxel.centre(seen)
        b = voxel.bounds(seen)
        if b is not None:
            lo, hi = b
            span = max(hi[i] - lo[i] for i in range(3))
            self.cam.distance = float(np.clip(span * 2.0, 4.0, 900.0))

    def draw_3d(self, size, over_ui):
        if self.dirty:
            self.remesh()
        boxes = []
        if not over_ui:
            if self.hover is not None:
                # brush-sized, because that is what a right-click now removes
                boxes.append(box_lines(self.erase_area(), self.brush, HOVER_COL))
            if self.block is not None:
                fits = bool(self.allowed(voxel.block_cells(self.block,
                                                           self.brush)))
                boxes.append(box_lines(self.block, self.brush,
                                       PLACE_COL if fits else BLOCKED_COL))
        aspect = size[0] / max(size[1], 1)
        if self.city_light:
            self.draw_city(size, aspect, boxes)
        else:
            self.renderer.draw(self.cam, aspect, self.show_grid, boxes,
                               self.show_wire)

    # --- the city-lighting preview ------------------------------------------

    def city_verts(self):
        """The model in metres, standing on a patch of ground.

        The city works in metres and this is the only place the two scales
        meet: everything the preview is meant to show — the shadow's length,
        how wide the ambient occlusion reads, how far the fog is — is decided
        in metres, so scaling here rather than teaching the renderer about
        cells is what makes the preview honest.
        """
        verts = voxel.model_vertices(self.voxels)
        verts = np.array(verts, dtype='f4', copy=True)
        verts[:, :3] *= voxel.CELL_M
        # the cell slot stays in cells: it is what the mosaic hashes, and it is
        # deliberately independent of how big a cell is in the world
        return np.vstack([ground_verts(CITY_GROUND_M), verts])

    def city_camera(self):
        """The editor's camera, in metres."""
        return OrbitCamera(target=self.cam.target * voxel.CELL_M,
                           yaw=self.cam.yaw, pitch=self.cam.pitch,
                           distance=self.cam.distance * voxel.CELL_M)

    def draw_city(self, size, aspect, boxes):
        from ..renderer import Renderer

        verts = self.city_verts() if (self.city is None or self.city_stale) else None
        extent = (-CITY_GROUND_M, -CITY_GROUND_M, CITY_GROUND_M, CITY_GROUND_M)
        if self.city is None:
            self.city = Renderer(self.ctx, verts,
                                 np.zeros((0, 5), dtype='f4'), extent)
        elif self.city_stale:
            self.city.set_geometry(verts, extent)
        self.city_stale = False

        cam = self.city_camera()
        cam.far = 4000.0
        # Whatever is bound, not ctx.screen: everything else here draws into the
        # current target, and the headless checks bind an offscreen one.
        # `ctx.screen` is None in a standalone context, so it is the fallback
        # rather than the first choice.
        target = self.ctx.fbo or self.ctx.screen
        self.city.render(target, cam, aspect, size=size)
        # the cursors go back on top afterwards: the city renderer owns its own
        # depth buffer and clears the target's, so there is nothing left to
        # depth-test them against. Their geometry is in cells, so the scale
        # rides in the matrix rather than in the vertices.
        scale = np.diag([voxel.CELL_M, voxel.CELL_M, voxel.CELL_M, 1.0])
        vp = to_gl(cam.projection(aspect) @ cam.view() @ scale)
        self.renderer.draw_cursors(vp, boxes)

    def draw_ui(self, ui, menubar, size, mouse):
        ui.begin(size)
        for i in range(voxel.N_HUES):
            x, y, w, h = swatch_rect(i)
            ui.rect(x, y, w, h, voxel.color_rgb(i, voxel.CENTRE_VALUE))
        ui.outline(*swatch_rect(self.hue), (1.0, 1.0, 1.0), px=3)
        self._draw_panel(ui)
        self._draw_brush(ui)
        self._draw_stats(ui)
        menubar.draw(size[0], mouse)
        ui.flush()

    def _draw_panel(self, ui):
        """The backing rectangle for the brush steppers and the stats.

        Measured off the widest line rather than a fixed width, since the stats
        change length as the model grows.
        """
        wide = max((ui.measure(line)[0] for line in self.stats), default=0)
        wide = max(wide, BRUSH_LBL_W + 2 * BRUSH_BTN + BRUSH_VAL_W)
        top = BRUSH_TOP - ROW_GAP
        bottom = STATS_TOP + len(self.stats) * STATS_LINE_H + ROW_GAP // 2
        ui.rect(0, top, wide + 2 * SWATCH_MARGIN, bottom - top, PANEL_COL)

    def _draw_stats(self, ui):
        for i, line in enumerate(self.stats):
            ui.text(line, SWATCH_MARGIN, STATS_TOP + i * STATS_LINE_H,
                    STATS_KEY_COL if i == 0 else STATS_COL)

    def _draw_brush(self, ui):
        """A '-' / size / '+' stepper per axis: the brush is sized per dimension."""
        for axis in range(3):
            mx, my, bw, bh = brush_btn_rect(axis, -1)
            ui.text_centred(BRUSH_AXES[axis], SWATCH_MARGIN + BRUSH_LBL_W // 2,
                            my + bh // 2, BRUSH_LBL_COL)
            for delta, glyph in ((-1, '-'), (1, '+')):
                x, y, w, h = brush_btn_rect(axis, delta)
                ui.rect(x, y, w, h, BRUSH_BTN_COL)
                ui.text_centred(glyph, x + w // 2, y + h // 2)
            ui.text_centred(str(self.brush[axis]),
                            mx + bw + BRUSH_VAL_W // 2, my + bh // 2)

    def release(self):
        self.renderer.release()
        if self.city is not None:
            self.city.release()


def _ask(ctx, size, save, title, default, patterns):
    """Run the file browser, with a UI of its own like the footprint picker."""
    ui = uikit.UI(ctx)
    try:
        return ask_path(ctx, ui, size, save, title, default, patterns)
    finally:
        ui.release()


def _menu_action(ed, action, ctx, size):
    """Apply a menubar action. Returns an outcome string, or None to carry on."""
    if action == 'new':
        ed.set_model({})
    elif action == 'open':
        p = _ask(ctx, size, False, 'Open model', ed.path,
                 [('JSON', '*.json'), ('All files', '*.*')])
        if p:
            try:
                ed.set_model(voxel.load(p))
                ed.path = p
                ed.set_footprint(_footprint_of(p))
                ed.frame()
                print(f'loaded {len(ed.voxels)} voxels from {p}')
            except (OSError, ValueError, KeyError) as exc:
                print(f'could not load {p}: {exc}')
    elif action == 'save':
        _save(ed, ed.path)
    elif action == 'save_as':
        p = _ask(ctx, size, True, 'Save model as', ed.path,
                 [('JSON', '*.json')])
        if p:
            ed.path = p
            _save(ed, p)
    elif action == 'export_obj':
        p = _ask(ctx, size, True, 'Export OBJ', 'model.obj',
                 [('OBJ', '*.obj')])
        if p:
            io.export_obj(ed.voxels, p)
    elif action == 'export_png':
        p = _ask(ctx, size, True, 'Export PNG', 'model.png',
                 [('PNG', '*.png')])
        if p:
            # redraw the 3D view alone so no UI ends up in the file
            ctx.screen.use()
            ctx.screen.clear(0.10, 0.11, 0.13, 1.0, depth=1.0)
            ed.draw_3d(size, over_ui=True)
            io.export_png(ctx, size, p)
    elif action == 'toggle_grid':
        ed.show_grid = not ed.show_grid
    elif action == 'undo':
        ed.undo()
    elif action == 'redo':
        ed.redo()
    elif action == 'eyedrop':
        ed.eyedrop()
    elif action == 'paint':
        ed.paint()
    elif action == 'fill_region':
        ed.fill_region()
    elif action == 'replace_hue':
        ed.replace_hue()
    elif action == 'fill_holes':
        ed.fill_holes()
    elif action == 'toggle_wire':
        ed.show_wire = not ed.show_wire
    elif action == 'toggle_city':
        ed.city_light = not ed.city_light
    elif action == 'reset_cam':
        ed.cam = OrbitCamera(target=voxel.centre(ed.voxels))
    elif action == 'frame':
        ed.frame()
    elif action == 'reset_brush':
        ed.brush = DEFAULT_BRUSH
    elif action == 'fp_choose':
        _choose_ground(ed, ctx, size)
    elif action == 'fp_open':
        p = _ask(ctx, size, False, 'Open footprint', footprints.OUT_DIR,
                 [('JSON', '*.json'), ('All files', '*.*')])
        if p:
            _set_ground_from(ed, p)
    elif action == 'fp_clear':
        ed.set_footprint(None)
        print('footprint cleared')
    elif action == 'menu':
        return 'menu'
    return None


def _choose_ground(ed, ctx, size):
    """Run the footprint picker and apply what comes back."""
    entries = footprints.list_models()
    if not entries:
        print(f'no footprints in {footprints.OUT_DIR}/ — run '
              f'`voxity --build-footprints` first')
        return
    ui = uikit.UI(ctx)
    try:
        picked = choose_footprint(ctx, ui, entries, size)
    finally:
        ui.release()
    if picked:
        ed.set_footprint(picked['cells'])
        ed.frame()
        print(f'building on {picked["name"]} '
              f'({picked["w"]}x{picked["h"]} m, {len(picked["cells"])} cells)')


def _set_ground_from(ed, path):
    """Use any model file's outline as the ground."""
    try:
        cells = footprints.load_cells(path)
    except (OSError, ValueError, KeyError) as exc:
        print(f'could not read {path}: {exc}')
        return
    if not cells:
        print(f'{path} has no cells to stand on')
        return
    ed.set_footprint(cells)
    ed.frame()
    print(f'building on {os.path.basename(path)} ({len(cells)} cells)')


def _save(ed, path):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    n = voxel.save(ed.voxels, path, ed.footprint)
    print(f'saved {n} voxels to {path}')


def run(ctx, size, model_path=DEFAULT_MODEL, frames=0, hud=None):
    """Drive the editor. Returns 'quit' or 'menu'."""
    ed = Editor(ctx, model_path)
    ui = uikit.UI(ctx)
    menubar = uikit.Menubar(ui, MENU_DEFS)
    clock = pygame.time.Clock()

    pygame.display.set_caption('voxity — editor')
    pygame.mouse.set_visible(True)
    pygame.event.set_grab(False)

    press = {}                     # button -> pixels dragged since the press
    outcome = 'quit'
    frame = 0
    running = True

    while running:
        clock.tick(60)
        frame += 1
        if frames and frame > frames:
            outcome = 'menu'
            break
        size = pygame.display.get_window_size()
        mouse = pygame.mouse.get_pos()
        click_add = click_del = click_paint = click_pick = False
        action = None

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.VIDEORESIZE:
                size = (max(320, event.w), max(240, event.h))
                ctx.viewport = (0, 0, *size)
            elif event.type == pygame.KEYDOWN:
                k = event.key
                ctrl = event.mod & pygame.KMOD_CTRL
                if ctrl and k == pygame.K_z:
                    ed.redo() if event.mod & pygame.KMOD_SHIFT else ed.undo()
                elif ctrl and k == pygame.K_y:
                    ed.redo()
                elif k == pygame.K_ESCAPE:
                    outcome = 'menu'
                    running = False
                elif k == pygame.K_g:
                    ed.show_grid = not ed.show_grid
                elif k == pygame.K_t:
                    ed.show_wire = not ed.show_wire
                elif k == pygame.K_c:
                    ed.city_light = not ed.city_light
                elif k == pygame.K_h:
                    ed.fill_holes()
                elif k == pygame.K_k:
                    ed.fill_region()
                elif k == pygame.K_r:
                    ed.replace_hue()
                elif k == pygame.K_i:
                    ed.eyedrop()
                elif k == pygame.K_f:
                    ed.frame()
                elif k == pygame.K_s:
                    _save(ed, ed.path)   # ctrl-S too: there is nothing else on S
                elif k == pygame.K_b:
                    _choose_ground(ed, ctx, size)
                elif k == pygame.K_l and not ctrl:
                    try:
                        ed.set_model(voxel.load(ed.path))
                        ed.set_footprint(_footprint_of(ed.path))
                        print(f'loaded {len(ed.voxels)} voxels from {ed.path}')
                    except (OSError, ValueError, KeyError) as exc:
                        print(f'could not load {ed.path}: {exc}')
                elif k == pygame.K_COMMA:              # ',' shrink every axis
                    ed.brush = resize_brush(ed.brush, None, -1)
                elif k == pygame.K_PERIOD:             # '.' grow every axis
                    ed.brush = resize_brush(ed.brush, None, 1)
                elif k in BRUSH_KEYS:                  # one axis at a time
                    d = -1 if event.mod & pygame.KMOD_SHIFT else 1
                    ed.brush = resize_brush(ed.brush, BRUSH_KEYS[k], d)
                elif pygame.K_0 <= k <= pygame.K_9:
                    n = k - pygame.K_0                 # 0..9
                    d = 10 if n == 0 else n            # 1..10
                    ed.hue = round((d - 1) / 9 * (voxel.N_HUES - 1))
            elif event.type == pygame.MOUSEBUTTONDOWN:
                if event.button == 1:
                    consumed, action = menubar.handle_click(*event.pos)
                    if not consumed:
                        press[1] = 0.0                 # a potential 3D click
                elif event.button in (2, 3):
                    menubar.open = None
                    press[event.button] = 0.0
                elif event.button == 4:
                    ed.cam.zoom(1)
                elif event.button == 5:
                    ed.cam.zoom(-1)
            elif event.type == pygame.MOUSEMOTION:
                dx, dy = event.rel
                if press.get(1) is not None and event.buttons[0]:
                    press[1] += abs(dx) + abs(dy)
                    ed.cam.orbit(dx, dy)
                for b, idx in ((2, 1), (3, 2)):
                    if press.get(b) is not None and event.buttons[idx]:
                        press[b] += abs(dx) + abs(dy)
                        ed.cam.pan(dx, dy)
            elif event.type == pygame.MOUSEBUTTONUP:
                moved = press.pop(event.button, None)
                if moved is not None and moved < DRAG_THRESHOLD:
                    if event.button == 1:
                        hit = swatch_at(*event.pos)
                        step = brush_btn_at(*event.pos)
                        if hit is not None:
                            ed.hue = hit
                        elif step is not None:
                            ed.brush = resize_brush(ed.brush, *step)
                        elif pygame.key.get_mods() & pygame.KMOD_SHIFT:
                            click_paint = True
                        else:
                            click_add = True
                    elif event.button == 2:
                        # middle *click*; middle-drag is still panning, which
                        # the drag threshold above has already ruled out
                        click_pick = True
                    elif event.button == 3:
                        click_del = True

        over_ui = mouse[1] < uikit.MENUBAR_H or menubar.open is not None
        if not over_ui:
            ed.aim(mouse, size)
        else:
            ed.hover = ed.block = None
        if not over_ui:
            if click_add:
                ed.add()
            if click_paint:
                ed.paint()
            if click_del:
                ed.delete()
            if click_pick:
                ed.eyedrop()

        result = _menu_action(ed, action, ctx, size)
        if result:
            outcome = result
            running = False

        ctx.screen.use()
        ctx.viewport = (0, 0, *size)
        ctx.screen.clear(0.10, 0.11, 0.13, 1.0, depth=1.0)
        ed.draw_3d(size, over_ui)
        ed.draw_ui(ui, menubar, size, mouse)
        if hud is not None:
            hud.draw(HELP, size, 'bl')
        pygame.display.flip()

    ed.release()
    ui.release()
    return outcome
