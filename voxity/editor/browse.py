"""Open and save, as a modal overlay over the editor's own window.

This replaces tkinter's `filedialog`, which was wrong here twice over. It draws
its own dialog on Unix — the grey listbox is Tk's, not the desktop's, and no
theme changes that — and it runs a nested Tcl event loop beside SDL's, which is
what left its buttons not responding to clicks.

So it is built the way `choose.py` is: one modal loop of our own, drawing with
`ui.py`, returning a value. Nothing else can be holding the mouse, because the
loop reading the mouse is this one.

Clicking is deliberately different in the two modes, because the useful action
differs. **Opening**, a click on a file *is* the answer, the way the footprint
picker works. **Saving**, a click only copies the name into the field — the
answer is what the field says when you press Enter, and a stray click should not
silently pick a different file to overwrite.
"""

import fnmatch
import os

import pygame

MARGIN = 40
ROW_H = 24
HEAD_H = 58
FOOT_H = 56
PAD = 14

BG = (0.09, 0.10, 0.12)
PANEL = (0.14, 0.15, 0.18)
EDGE = (0.32, 0.35, 0.41)
ROW_HI = (0.24, 0.31, 0.40)
SEL = (0.20, 0.38, 0.30)
DIR_COL = (0.72, 0.82, 0.95)
FILE_COL = (0.86, 0.88, 0.91)
DIM = (0.55, 0.58, 0.64)
FIELD = (0.08, 0.09, 0.11)
CARET = (0.55, 0.95, 0.70)


def _suffixes(patterns):
    """The globs in a tkinter-style pattern list, or None for 'anything'."""
    globs = []
    for _, pat in patterns or ():
        for part in str(pat).split():
            if part in ('*', '*.*'):
                return None
            globs.append(part)
    return globs or None


def listing(directory, globs):
    """(dirs, files) in `directory`, sorted, hiding dotfiles and unreadables."""
    try:
        names = sorted(os.listdir(directory), key=str.lower)
    except OSError:
        return [], []
    dirs, files = [], []
    for name in names:
        if name.startswith('.'):
            continue
        full = os.path.join(directory, name)
        if os.path.isdir(full):
            dirs.append(name)
        elif globs is None or any(fnmatch.fnmatch(name.lower(), g.lower())
                                  for g in globs):
            files.append(name)
    return dirs, files


def _start(default, save):
    """Where to open, and what to prefill the name with."""
    default = default or ''
    if default and os.path.isdir(default):
        return os.path.abspath(default), ''
    head, tail = os.path.split(default)
    directory = os.path.abspath(head) if head else os.path.abspath('.')
    if not os.path.isdir(directory):
        directory = os.path.abspath('.')
    return directory, (tail if save else '')


def _fit(ui, text, width):
    """`text` shortened from the left until it fits, so the end stays visible."""
    if ui.measure(text)[0] <= width:
        return text
    while text and ui.measure('...' + text)[0] > width:
        text = text[1:]
    return '...' + text


def layout(size, save):
    """(x, y, w, h, list_top, rows_per_page) for a window of `size`.

    Shared by the loop and by `paint`, so a hit test and what is drawn cannot
    disagree — the whole dialog is one rectangle and a column of rows.
    """
    x0, y0 = MARGIN, MARGIN
    w = max(320, size[0] - 2 * MARGIN)
    h = max(240, size[1] - 2 * MARGIN)
    list_top = y0 + HEAD_H
    list_h = h - HEAD_H - (FOOT_H if save else PAD + ROW_H)
    return x0, y0, w, h, list_top, max(1, list_h // ROW_H)


def row_at(size, save, rows, scroll, mouse):
    """Index of the row under the cursor, or None."""
    x0, _, w, _, list_top, per_page = layout(size, save)
    for i in range(scroll, min(len(rows), scroll + per_page)):
        ry = list_top + (i - scroll) * ROW_H
        if x0 <= mouse[0] <= x0 + w and ry <= mouse[1] < ry + ROW_H:
            return i
    return None


def paint(ui, size, save, title, directory, rows, scroll, hot, name, frame=0):
    """Queue the whole dialog into `ui`. Separate from the loop so it can be
    drawn into an offscreen buffer without a window."""
    x0, y0, w, h, list_top, per_page = layout(size, save)
    ui.begin(size)
    ui.rect(x0, y0, w, h, PANEL)
    ui.outline(x0, y0, w, h, EDGE, px=1)

    tw, _ = ui.text(title, x0 + PAD, y0 + 10, (0.93, 0.94, 0.96))
    ui.text('Enter to confirm   ESC to cancel   wheel to scroll'
            if save else 'click a file to open it   ESC to cancel',
            x0 + PAD + tw + 24, y0 + 10, DIM)
    ui.text(_fit(ui, directory, w - 2 * PAD), x0 + PAD, y0 + 32, DIM)

    for i in range(scroll, min(len(rows), scroll + per_page)):
        ry = list_top + (i - scroll) * ROW_H
        row = rows[i]
        if i == hot:
            ui.rect(x0 + 1, ry, w - 2, ROW_H, ROW_HI)
        elif save and row == name:
            ui.rect(x0 + 1, ry, w - 2, ROW_H, SEL)
        is_dir = i == 0 or row.endswith('/')
        ui.text(row, x0 + PAD + 6, ry + 4, DIR_COL if is_dir else FILE_COL)

    if not rows[1:]:
        ui.text('nothing here that matches', x0 + PAD + 6,
                list_top + ROW_H + 6, DIM)

    if save:
        fy = y0 + h - FOOT_H + PAD
        ui.text('name', x0 + PAD, fy + 5, DIM)
        fx = x0 + PAD + 52
        fw = w - (fx - x0) - PAD
        ui.rect(fx, fy, fw, ROW_H, FIELD)
        ui.outline(fx, fy, fw, ROW_H, EDGE, px=1)
        shown = _fit(ui, name, fw - 16)
        nw, _ = ui.text(shown, fx + 6, fy + 4, (0.95, 0.96, 0.98))
        if (frame // 30) % 2 == 0:            # a caret, so it reads as typable
            ui.rect(fx + 8 + nw, fy + 4, 2, ROW_H - 8, CARET)


def ask_path(ctx, ui, size, save, title, default, patterns, frames=0):
    """Run the dialog. Returns an absolute path, or None if it was dismissed."""
    globs = _suffixes(patterns)
    directory, name = _start(default, save)
    clock = pygame.time.Clock()
    scroll = 0
    chosen = None
    running = True
    frame = 0

    while running:
        clock.tick(60)
        frame += 1
        if frames and frame > frames:
            break
        size = pygame.display.get_window_size()
        mouse = pygame.mouse.get_pos()
        dirs, files = listing(directory, globs)
        rows = ['..'] + [d + '/' for d in dirs] + files

        _, _, _, _, _, per_page = layout(size, save)
        max_scroll = max(0, len(rows) - per_page)
        scroll = min(scroll, max_scroll)
        hot = row_at(size, save, rows, scroll, mouse)

        def enter(idx, rows=rows):
            """Act on row `idx`; returns a path to finish with, or None.

            `rows` is bound as a default rather than closed over: it is rebuilt
            every frame, and a closure over it would be a live reference to
            whatever the *next* frame happens to be listing.
            """
            nonlocal directory, scroll, name
            row = rows[idx]
            if idx == 0:
                directory = os.path.dirname(directory) or directory
                scroll = 0
                return None
            if row.endswith('/'):
                directory = os.path.join(directory, row[:-1])
                scroll = 0
                return None
            if save:
                name = row
                return None
            return os.path.join(directory, row)

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.event.post(pygame.event.Event(pygame.QUIT))
                running = False
            elif event.type == pygame.VIDEORESIZE:
                size = (max(320, event.w), max(240, event.h))
                ctx.viewport = (0, 0, *size)
            elif event.type == pygame.MOUSEBUTTONDOWN:
                if event.button == 4:
                    scroll = max(0, scroll - 3)
                elif event.button == 5:
                    scroll = min(max_scroll, scroll + 3)
                elif event.button == 1 and hot is not None:
                    got = enter(hot)
                    if got:
                        chosen, running = got, False
            elif event.type == pygame.KEYDOWN:
                k = event.key
                if k == pygame.K_ESCAPE:
                    running = False
                elif k == pygame.K_UP:
                    scroll = max(0, scroll - 1)
                elif k == pygame.K_DOWN:
                    scroll = min(max_scroll, scroll + 1)
                elif k == pygame.K_PAGEUP:
                    scroll = max(0, scroll - per_page)
                elif k == pygame.K_PAGEDOWN:
                    scroll = min(max_scroll, scroll + per_page)
                elif k == pygame.K_BACKSPACE:
                    if save and name:
                        name = name[:-1]
                    elif not save:
                        directory = os.path.dirname(directory) or directory
                        scroll = 0
                elif k in (pygame.K_RETURN, pygame.K_KP_ENTER):
                    if save and name:
                        chosen, running = os.path.join(directory, name), False
                elif save and event.unicode and event.unicode.isprintable():
                    name += event.unicode

        ctx.screen.use()
        ctx.viewport = (0, 0, *size)
        ctx.screen.clear(*BG, 1.0, depth=1.0)
        paint(ui, size, save, title, directory, rows, scroll, hot, name, frame)
        ui.flush()
        pygame.display.flip()

    return chosen
