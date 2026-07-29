"""The screen you land on: fly the city, or open the editor.

Owns its own event loop against the already-open window, the same way
mapview.py does, and returns one of 'play' / 'editor' / 'quit'.
"""

import moderngl
import pygame

from . import ui as uikit

BG = (0.07, 0.08, 0.10, 1.0)

TITLE = 'voxity'
SUBTITLE = 'a city out of OpenStreetMap, built out of voxels'

ITEMS = [
    ('play', 'Fly the city', 'pick a square of the extract and fly through it'),
    ('editor', 'Voxel editor', 'build the models the city is made of'),
    ('quit', 'Quit', ''),
]

BTN_W, BTN_H, BTN_GAP = 560, 62, 14
IDLE = (0.15, 0.16, 0.19)
HOVER = (0.22, 0.28, 0.36)
EDGE = (0.42, 0.52, 0.62)
FG = (0.93, 0.94, 0.96)
DIM = (0.58, 0.62, 0.68)


def _layout(size):
    """Button rectangles, centred as a block under the title."""
    total = len(ITEMS) * BTN_H + (len(ITEMS) - 1) * BTN_GAP
    x = (size[0] - BTN_W) // 2
    y = max(uikit.MENUBAR_H + 120, (size[1] - total) // 2 + 40)
    return [(x, y + i * (BTN_H + BTN_GAP), BTN_W, BTN_H)
            for i in range(len(ITEMS))]


def _hit(rects, pos):
    for i, (x, y, w, h) in enumerate(rects):
        if x <= pos[0] < x + w and y <= pos[1] < y + h:
            return i
    return None


def choose(ctx, size, frames=0):
    """Run the start screen. Returns 'play', 'editor' or 'quit'."""
    ui = uikit.UI(ctx, font_size=18)
    clock = pygame.time.Clock()

    pygame.display.set_caption('voxity')
    pygame.mouse.set_visible(True)
    pygame.event.set_grab(False)

    sel = 0
    result = 'quit'
    frame = 0
    running = True

    while running:
        clock.tick(60)
        frame += 1
        if frames and frame > frames:
            # smoke tests want the whole chain, so hand on to the city rather
            # than quitting here and never exercising the picker
            result = 'play'
            break
        size = pygame.display.get_window_size()
        rects = _layout(size)
        mouse = pygame.mouse.get_pos()

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.VIDEORESIZE:
                size = (max(320, event.w), max(240, event.h))
                ctx.viewport = (0, 0, *size)
            elif event.type == pygame.MOUSEMOTION:
                over = _hit(rects, event.pos)
                if over is not None:
                    sel = over
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                over = _hit(rects, event.pos)
                if over is not None:
                    result = ITEMS[over][0]
                    running = False
            elif event.type == pygame.KEYDOWN:
                k = event.key
                if k == pygame.K_ESCAPE:
                    running = False
                elif k in (pygame.K_DOWN, pygame.K_TAB):
                    sel = (sel + 1) % len(ITEMS)
                elif k == pygame.K_UP:
                    sel = (sel - 1) % len(ITEMS)
                elif k in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                    result = ITEMS[sel][0]
                    running = False

        hovered = _hit(rects, mouse)
        if hovered is not None:
            sel = hovered

        ctx.screen.use()
        ctx.viewport = (0, 0, *size)
        ctx.screen.clear(*BG, depth=1.0)
        ctx.disable(moderngl.DEPTH_TEST | moderngl.CULL_FACE)

        ui.begin(size)
        top = rects[0][1]
        ui.text_centred(TITLE, size[0] // 2, top - 96, FG, big=True)
        ui.text_centred(SUBTITLE, size[0] // 2, top - 52, DIM)
        for i, ((x, y, w, h), (_, label, hint)) in enumerate(
                zip(rects, ITEMS, strict=True)):
            ui.rect(x, y, w, h, HOVER if i == sel else IDLE)
            if i == sel:
                ui.outline(x, y, w, h, EDGE, px=2)
            ui.text(label, x + 22, y + (12 if hint else (h - 22) // 2), FG)
            if hint:
                ui.text(hint, x + 22, y + h - 26, DIM)
        ui.flush()
        pygame.display.flip()

    ui.release()
    return result
