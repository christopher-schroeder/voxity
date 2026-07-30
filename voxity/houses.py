"""Default voxel houses standing on the footprints the survey found.

`--build-footprints` answers "what shapes does this city repeat"; this answers
"what should one of them look like before anybody has drawn it by hand". Every
model it writes records its footprint, so opening one in the editor gives you
the plan as the ground and the house as a starting point — these are meant to be
replaced, not kept.

Two ideas do all the work, and both exist because a footprint can be an L or a
chamfered octagon and not just a box:

* **Walls are the footprint's perimeter.** Any cell with a side on the outside
  gets a wall, so a courtyard gets walls round it too, and the model is hollow —
  the interior is a sealed cavity, which `voxel.exterior_air` drops entirely.
* **Roofs are repeated erosion.** Stacking successively eroded copies of the
  plan gives a stepped pitch that follows whatever shape it is standing on:
  eroding on both axes hips it, eroding across the short axis alone leaves a
  ridge along the long one. Fitting a pitch to a bounding box instead — which is
  what build.py's gable fitter does, and rightly, for a solid extrusion — loses
  an L's inner corner.

Storeys are spread across a plan's variants rather than drawn at random. The
city picks a house by how close its height is to the building's, so a plan whose
houses all came out three storeys would be matched by almost nothing.
"""

import hashlib
import os

import numpy as np

from . import voxel

OUT_DIR = 'models/houses'

# Everything dimensional here is **metres**, converted to cells against
# `voxel.CELL_M` at the point of use. It used to be cells, which was the same
# thing while a cell was a metre and quietly became a house with 25 cm storeys
# when it stopped being one.
STOREY_M = 3.0                # floor to floor
PLINTH_M = 0.5                # the base course the walls stand on
WINDOW_W_M, WINDOW_H_M = 1.0, 1.4
WINDOW_SILL_M = 0.9           # above the floor of its storey
WINDOW_PITCH_M = 2.4          # centre to centre along a wall
DOOR_W_M, DOOR_H_M = 1.0, 2.1
ROOF_MAX_M = 3.5              # a roof may rise this far before it is cut flat

MIN_STOREYS, MAX_STOREYS = 1, 5
VARIANTS = 5                  # models written per footprint

STYLES = ('gabled', 'hipped', 'flat')


def _cells(metres, cell, least=1):
    """`metres` as a whole number of cells, never below `least`."""
    return max(least, round(metres / cell))

# Hue indices into voxel's 32-step wheel; saturation and value are not ours to
# choose (see voxel.py), so hue is the only thing that tells two parts apart.
WALL_HUES = (2, 3, 4, 5, 6, 19, 20, 21, 25)
ROOF_HUES = (0, 1, 22, 29, 30, 31)
GLASS_HUES = (16, 17, 18, 19, 11)
DOOR_HUES = (26, 27, 28, 0)
HUE_GAP = 5                   # hue steps a detail must clear its wall by

_STEPS = {None: ((1, 0), (-1, 0), (0, 1), (0, -1)),
          0: ((1, 0), (-1, 0)),
          1: ((0, 1), (0, -1))}


# --- shape helpers ----------------------------------------------------------

def hue_gap(a, b):
    """Steps between two hues the short way round the wheel."""
    d = (a - b) % voxel.N_HUES
    return min(d, voxel.N_HUES - d)


def contrast(wall, options, gap=HUE_GAP):
    """`options` far enough from `wall` to be seen, or the opposite hue.

    Value and saturation are fixed by the palette, so hue is the only thing
    telling a window from the wall around it — pick blue glass for a blue wall
    and the windows simply vanish.
    """
    ok = [h for h in options if hue_gap(h, wall) >= gap]
    return ok or [(wall + voxel.N_HUES // 2) % voxel.N_HUES]


def perimeter(cells):
    """Cells with at least one side on the outside — where the walls go."""
    return {c for c in cells
            if any((c[0] + dx, c[1] + dz) not in cells
                   for dx, dz in _STEPS[None])}


def erode(cells, axis=None):
    """Cells all of whose neighbours are inside; `axis` erodes on one axis only."""
    return {c for c in cells
            if all((c[0] + dx, c[1] + dz) in cells for dx, dz in _STEPS[axis])}


def wall_axis(cell, cells):
    """The axis a perimeter cell's wall runs along, or None at a corner.

    A cell open towards +x or -x faces along x, so its wall runs along z. One
    open both ways is an outside corner, and a window there would be a hole
    through two walls at once.
    """
    x, z = cell
    open_x = (x + 1, z) not in cells or (x - 1, z) not in cells
    open_z = (x, z + 1) not in cells or (x, z - 1) not in cells
    if open_x and not open_z:
        return 1
    if open_z and not open_x:
        return 0
    return None


def span(cells):
    """(width, depth) of the footprint in cells."""
    xs = [c[0] for c in cells]
    zs = [c[1] for c in cells]
    return max(xs) - min(xs) + 1, max(zs) - min(zs) + 1


# --- the parts --------------------------------------------------------------

def roof(cells, y, style, hue, rise, parapet=1):
    """Voxels of a roof standing on `cells` with its deck at height `y`.

    `rise` cuts the pitch off flat. Left to run, erosion stops only when the
    plan is used up, which is half the short span — a 45-degree pitch, real
    enough on paper but on a deep plan it buries the storeys underneath it.
    """
    out = {(x, y, z): hue for x, z in cells}
    if style == 'flat':
        for x, z in perimeter(cells):       # a parapet, so it reads as a roof
            for j in range(1, parapet + 1):
                out[(x, y + j, z)] = hue
        return out
    axis = None
    if style == 'gabled':
        w, d = span(cells)
        axis = 0 if w <= d else 1           # ridge along the long side
    layer, top = cells, y
    while top - y < rise:
        layer = erode(layer, axis)
        if not layer:
            break
        top += 1
        out.update({(x, top, z): hue for x, z in layer})
    return out


def add_windows(v, cells, walls, storeys, hue, phase, cell, base):
    """A band of windows per storey, on the straight parts of every wall.

    A window is a rectangle of cells now rather than the single cell it was at a
    metre per cell — 1.0 by 1.4 m, repeating every 2.4 m along the wall, with
    its sill 0.9 m above its own floor.
    """
    storey = _cells(STOREY_M, cell)
    ww, wh = _cells(WINDOW_W_M, cell), _cells(WINDOW_H_M, cell)
    pitch = max(ww + 1, _cells(WINDOW_PITCH_M, cell))
    sill = _cells(WINDOW_SILL_M, cell)
    for s in range(storeys):
        y0 = base + s * storey + sill
        for c in walls:
            a = wall_axis(c, cells)
            # `c[a]` runs along the wall, so one modulo puts a window every
            # `pitch` cells and keeps it `ww` cells wide wherever the wall goes
            if a is not None and (c[a] + phase) % pitch < ww:
                for j in range(wh):
                    v[(c[0], y0 + j, c[1])] = hue


def add_door(v, cells, walls, hue, cell, base):
    """One door, in the middle of the furthest wall that faces +z.

    Placed rather than drawn from the rng: a door is the one feature read as
    deliberate, and one in a corner looks like a mistake rather than variety.
    """
    front = [c for c in walls
             if wall_axis(c, cells) == 0 and (c[0], c[1] + 1) not in cells]
    if not front:
        front = [c for c in walls if wall_axis(c, cells) is not None]
    if not front:
        return
    z = max(c[1] for c in front)
    row = sorted(c[0] for c in front if c[1] == z)
    x = row[len(row) // 2]
    dw, dh = _cells(DOOR_W_M, cell), _cells(DOOR_H_M, cell)
    for i in range(dw):
        cx = x - dw // 2 + i
        if (cx, z) not in cells:
            continue
        for y in range(base, base + dh):
            v[(cx, y, z)] = hue


# --- a whole house ----------------------------------------------------------

def palette(seed):
    """The four hues one house is built from, as {'wall', 'roof', 'glass', 'door'}.

    Split out of `house` so it can be checked on its own: the wall hue is not
    recoverable from a finished model — the plinth takes the roof's hue and is a
    solid slab, so on a low house the commonest hue in the model is the roof.
    """
    rng = np.random.default_rng(seed)
    wall = int(rng.choice(WALL_HUES))
    return {'wall': wall,
            'roof': int(rng.choice(contrast(wall, ROOF_HUES))),
            'glass': int(rng.choice(contrast(wall, GLASS_HUES))),
            'door': int(rng.choice(contrast(wall, DOOR_HUES)))}


def house(cells, storeys=2, style='gabled', seed=0, cell=voxel.CELL_M):
    """A default house standing on `cells`, as `{(x, y, z): hue}`.

    Hollow on purpose: only the perimeter carries walls, so what is written is
    roughly what you can see. The plinth is solid, which is what stops you
    looking up into an empty shell from below.
    """
    hues = palette(seed)
    wall, roof_hue = hues['wall'], hues['roof']
    glass, door = hues['glass'], hues['door']
    rng = np.random.default_rng(seed + 7919)

    storey = _cells(STOREY_M, cell)
    plinth = _cells(PLINTH_M, cell)
    phase = int(rng.integers(0, max(2, _cells(WINDOW_PITCH_M, cell))))

    cells = set(cells)
    walls = perimeter(cells)
    # the plinth takes the roof's hue rather than one of its own: a course of
    # the roof material at the base reads as a base, while a third colour down
    # there reads as a band of something growing round the house
    v = {(x, y, z): roof_hue for x, z in cells for y in range(plinth)}
    top = plinth + storeys * storey
    for y in range(plinth, top):
        for x, z in walls:
            v[(x, y, z)] = wall
    add_windows(v, cells, walls, storeys, glass, phase, cell, plinth)
    add_door(v, cells, walls, door, cell, plinth)
    # the pitch is capped by the wall it stands on as well as in metres, so a
    # bungalow does not disappear under its own roof
    rise = min(_cells(ROOF_MAX_M, cell),
               max(_cells(0.5, cell), (top - plinth) // 3))
    v.update(roof(cells, top, style, roof_hue, rise,
                  parapet=_cells(0.4, cell)))
    return v


def height(voxels):
    """Cells from the ground to the top of the model."""
    b = voxel.bounds(voxels)
    return 0 if b is None else b[1][1]


def variants(cells, count=VARIANTS, seed=0, cell=voxel.CELL_M):
    """`count` default houses for one footprint, as dicts describing each.

    Storeys walk the range instead of being drawn, so a plan always gets one
    house of each size — see the module docstring for why that matters.
    """
    out = []
    steps = MAX_STOREYS - MIN_STOREYS + 1
    for i in range(count):
        storeys = MIN_STOREYS + i % steps
        style = STYLES[i % len(STYLES)]
        v = house(cells, storeys, style, seed + i, cell)
        out.append({'voxels': v, 'storeys': storeys, 'style': style,
                    'height': height(v), 'variant': i})
    return out


def seed_for(name):
    """A stable seed from a footprint's name, so a re-run writes the same houses."""
    return int(hashlib.sha1(name.encode()).hexdigest()[:8], 16)


def write(entries, out_dir=OUT_DIR, count=VARIANTS, cell=voxel.CELL_M,
          verbose=True):
    """Write `count` houses for each footprint in `entries` (see footprints.list_models).

    Every house records its footprint, which is what makes it re-openable in the
    editor with the right ground and what `place.py` matches buildings against.
    Names carry the plan they stand on so the directory can be read by eye; the
    city does not parse them, it reads the footprint out of each file.
    """
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for e in entries:
        stem = e['name'][:-5].replace('footprint-', '')
        for h in variants(e['cells'], count, seed_for(e['name']), cell):
            name = f'house-{stem}-{h["variant"]}-{h["style"]}.json'
            voxel.save(h['voxels'], os.path.join(out_dir, name), e['cells'])
            written.append(dict(h, file=name, plan=e['name']))
        if verbose:
            hs = ', '.join(f'{h["height"] * cell:.0f}m'
                           for h in written[-count:])
            print(f'  {e["name"]:34s} {count} houses, heights {hs}')
    if verbose:
        print(f'  wrote {len(written)} houses for {len(entries)} footprints '
              f'to {out_dir}/')
    return written
