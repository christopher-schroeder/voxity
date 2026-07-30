"""Pick the footprint to build on, as shapes rather than as filenames.

A modal overlay over the editor's own window, like startscreen.py and mapview.py
own their loops. It has to be visual: the whole point of the survey is that these
are *shapes*, and a list of `footprint-11-0-15x12.json` tells you nothing about
which one is the L.

Thumbnails are drawn as one rectangle per run of filled cells in a row, not one
per cell. A 30 x 6 plan is 180 cells but only 6 runs, and `ui.UI` batches every
solid on screen into a single buffer capped at `MAX_QUADS` — per-cell drawing
overruns that with a page of plans and silently loses the last few.
"""

import pygame

MARGIN = 26
COLS = 6
TILE_W, TILE_H = 168, 116
LABEL_H = 34
GAP = 10

BG = (0.09, 0.10, 0.12)
TILE_BG = (0.16, 0.17, 0.20)
TILE_HI = (0.24, 0.30, 0.36)
CELL_COL = (0.55, 0.72, 0.46)
CELL_HI = (0.70, 0.92, 0.58)
EDGE_HI = (0.45, 0.95, 0.60)


def _runs(cells):
    """(z, x0, x1) horizontal runs of a footprint, for cheap thumbnails."""
    by_row = {}
    for x, z in cells:
        by_row.setdefault(z, []).append(x)
    out = []
    for z, xs in by_row.items():
        xs.sort()
        start = prev = xs[0]
        for x in xs[1:]:
            if x != prev + 1:
                out.append((z, start, prev))
                start = x
            prev = x
        out.append((z, start, prev))
    return out


def _layout(n, size):
    """Tile rectangles for `n` entries, and the total height of the grid."""
    cols = max(1, min(COLS, (size[0] - 2 * MARGIN + GAP) // (TILE_W + GAP)))
    cell_h = TILE_H + LABEL_H + GAP
    rects = []
    for i in range(n):
        rects.append((MARGIN + (i % cols) * (TILE_W + GAP),
                      (i // cols) * cell_h,
                      TILE_W, TILE_H + LABEL_H))
    rows = (n + cols - 1) // cols
    return rects, rows * cell_h


def _draw_thumb(ui, entry, rect, hot):
    x, y, w, h = rect
    ui.rect(x, y, w, h, TILE_HI if hot else TILE_BG)
    cells = entry['cells']
    ew, eh = entry['w'], entry['h']
    scale = max(1, int(min((TILE_W - 24) / ew, (TILE_H - 24) / eh)))
    ox = x + (w - ew * scale) // 2
    oy = y + (TILE_H - eh * scale) // 2
    x0 = min(c[0] for c in cells)
    z0 = min(c[1] for c in cells)
    col = CELL_HI if hot else CELL_COL
    for z, a, b in _runs(cells):
        ui.rect(ox + (a - x0) * scale, oy + (z - z0) * scale,
                (b - a + 1) * scale, scale, col)
    if hot:
        ui.outline(x, y, w, h, EDGE_HI, px=2)
    label = f'{ew}x{eh} m'
    if entry.get('buildings'):
        label += f'   {entry["buildings"]:,}'
    ui.text_centred(label, x + w // 2, y + TILE_H + LABEL_H // 2 - 2,
                    (0.80, 0.84, 0.88))


def choose_footprint(ctx, ui, entries, size, frames=0):
    """Run the picker. Returns the chosen entry, or None if it was dismissed."""
    if not entries:
        return None
    clock = pygame.time.Clock()
    scroll = 0
    chosen = None
    frame = 0
    running = True

    while running:
        clock.tick(60)
        frame += 1
        if frames and frame > frames:
            break
        size = pygame.display.get_window_size()
        mouse = pygame.mouse.get_pos()
        rects, total = _layout(len(entries), size)
        top = MARGIN + 40
        view = max(1, size[1] - top - MARGIN)
        max_scroll = max(0, total - view)

        hot = None
        for i, (rx, ry, rw, rh) in enumerate(rects):
            ry += top - scroll
            if rx <= mouse[0] <= rx + rw and ry <= mouse[1] <= ry + rh:
                hot = i

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.event.post(pygame.event.Event(pygame.QUIT))
                running = False
            elif event.type == pygame.VIDEORESIZE:
                size = (max(320, event.w), max(240, event.h))
                ctx.viewport = (0, 0, *size)
            elif event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_ESCAPE, pygame.K_b):
                    running = False
                elif event.key == pygame.K_DOWN:
                    scroll = min(max_scroll, scroll + 60)
                elif event.key == pygame.K_UP:
                    scroll = max(0, scroll - 60)
            elif event.type == pygame.MOUSEBUTTONDOWN:
                if event.button == 4:
                    scroll = max(0, scroll - 60)
                elif event.button == 5:
                    scroll = min(max_scroll, scroll + 60)
                elif event.button == 1 and hot is not None:
                    chosen = entries[hot]
                    running = False

        ctx.screen.use()
        ctx.viewport = (0, 0, *size)
        ctx.screen.clear(*BG, 1.0, depth=1.0)
        ui.begin(size)
        title = 'choose a footprint to build on'
        tw, _ = ui.text(title, MARGIN, MARGIN - 6, (0.92, 0.93, 0.95))
        # measured, not guessed: a hard-coded offset overlaps the title the
        # moment the font differs from the one it was eyeballed against
        ui.text('click one   ESC to cancel   wheel to scroll',
                MARGIN + tw + 30, MARGIN - 6, (0.55, 0.58, 0.63))
        for i, (rx, ry, rw, rh) in enumerate(rects):
            ry += top - scroll
            if ry + rh < top or ry > size[1]:          # off-screen, skip
                continue
            _draw_thumb(ui, entries[i], (rx, ry, rw, rh), i == hot)
        ui.flush()
        pygame.display.flip()

    return chosen
