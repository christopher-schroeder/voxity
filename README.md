# voxity

Two halves of one thing: a 3D city built out of an `.osm.pbf` extract that you
fly through, and a **voxel editor** for the models it is made of. pygame for
the window and input, moderngl for the rendering, one binary and one GL context
for both.

Start it with no arguments and you land on a menu. **Fly the city** gives you a
flat map of the whole extract — the city at a glance, roads and water and parks
— to pick your square off; pick one and it pulls out everything inside,
extrudes the buildings, lays out the roads and water, and renders the result
with a shadow-mapped sun. **Voxel editor** opens a grid you build blocky models
on, and `ESC` from either takes you back to the menu.

The halves meet in the middle: the city surveys the floor plans it repeats, the
editor builds houses on those plans, and buildings matching one are drawn as the
voxel house instead of an extruded block.

```
./env/bin/voxity                                  # menu
./env/bin/voxity --place rathaus --size 1200      # straight into the city
./env/bin/voxity --center 53.5503,9.9937 --size 2000
./env/bin/voxity --bbox 9.98,53.54,10.00,53.56
./env/bin/voxity --editor                         # straight into the editor
```

## The editor

Unit cubes on a grid, an orbiting camera, and a brush that stamps a solid box
of them. **A cell is 25 cm**, so a storey is twelve of them and a window four
across — the floor grid is drawn one line per metre to keep that readable. The box is sized **per axis**, 1 to 32 cells each — walls, floors and
pillars are the shapes you actually build, and none of them is a cube — with a
`- n +` stepper per dimension under the palette. You pick the **hue**; the
brightness is not yours to choose — it is a hash of the cell's position, so
neighbouring voxels vary slightly and a given cell always looks the same. That
is what gives a flat wall its mosaic. The position is the cell's place in the
*model*, not in the world, so the grain turns with a house when the city stands
it on a street that runs at an angle.

| | |
|---|---|
| left-drag | orbit |
| right-drag | pan |
| wheel | zoom |
| left-click | add a block on the green highlight |
| shift left-click | repaint what is under the red highlight |
| right-click | delete what is under the red highlight |
| middle-click, `I` | take the hue of the voxel under the cursor |
| `ctrl-Z` `ctrl-Y` | undo / redo |
| `K` | flood the connected same-hue run with the current hue |
| `R` | give every voxel of that hue the current one |
| `H` | fill the sealed holes inside the model |
| click a swatch, `1`–`9` `0` | set the hue |
| `X` `Y` `Z` | grow the brush along that axis (`shift` to shrink) |
| click a stepper | the same, with the mouse |
| `,` `.` | shrink / grow all three axes at once |
| `G` | floor grid |
| `T` | the triangles, drawn over the model |
| `C` | light it the way the city will |
| `F` | frame the model |
| `S` `L` | save / load the current model |
| `B` | choose a footprint to build on |
| `ESC` | back to the menu |

Under the brush steppers is what the model currently costs: **triangles and
vertices**, the model's **height** in cells, and its base dimensions and voxel
count. Both counts are there because between them they show the greedy mesher
working — a flat wall of one hue is two triangles however many voxels it is, so
a voxel count that climbs while the triangle count does not is merging doing its
job. The numbers are what the mesher just produced, not an estimate, so they
update the moment you place or delete a block.

`T`, or **View → Show Triangles**, draws those triangles over the model, so you
can see where merging did and did not happen: one wall of a single hue comes out
as a couple of large quads, and every window in it as a quad of its own. It is
the same buffer the model is drawn from, so it is the real triangulation and not
a redrawing of it, and the depth test stays on so the far side stays hidden.

`C`, or **View → City Lighting**, stands the model on a patch of ground and
draws it through the game's own renderer — the real sun, shadows, ambient
occlusion and colour grading, not an approximation of them. It is there because
the editor's normal shading is deliberately flat: you are picking hues from a
palette, and what is on screen has to *be* the hue you clicked, which it cannot
be once a sun and a tone curve have been through it. So build in the flat view
and press `C` to see what the city will make of it.

**Deleting and painting use the brush too**, so the red highlight is always
exactly what a right-click removes or a shift-click recolours — one control
sizes both what you place and what you take away. Everything that changes the
model is undoable, including New and Open; history is kept as differences rather
than snapshots, so a hundred strokes cost a few thousand cells rather than a
hundred copies of a 24,000-voxel house.

**Fill holes** (`H`) fills the cavities sealed inside the model. They are
invisible either way — the mesher already refuses to draw faces against them —
but it is the difference between a hollow house and a solid one once something
slices it open. **Fill region** (`K`) is a paint bucket: it stops at the first
change of hue, so it colours one wall rather than the building.

The block moves in **single cells** at any size, rather than snapping to a grid
of its own size: it centres on the cell under the cursor, and sits on the empty
side of whatever face you are pointing at. A tall brush aimed at a low wall is
held above the floor instead of straddling it, so the green outline is always
exactly what a click will place. **View → Reset Brush** puts it back to 1×1×1.

### Building on a footprint

`B`, or **Ground → Choose Footprint**, opens a picker of the plans the survey
found (see below) and makes one the **ground**: its outline is drawn on the
floor, and it becomes the limit you build inside. Voxels outside it are refused
— the placement box turns grey where a click would do nothing — while height
stays free, so you build up rather than out. **Ground → Clear Footprint** lifts
the limit again, and **Open Footprint File** takes any model at all, so a
finished house can serve as the outline for the next one.

Choosing a ground never deletes anything: voxels already outside it are left
where they are and merely counted on the console. The footprint is saved into
the model, so reopening a half-built house puts its limits back.

**File** does New / Open / Save / Save As / Export OBJ / Export PNG through a
file browser drawn in the window itself — click a folder to enter it, `..` or
backspace to go up, and when saving, type the name and press Enter. Models are
JSON under `models/`.

Two passes turn the voxel dict into as few triangles as possible without
changing what you see. **Exterior-cavity culling** flood-fills empty space
inward from outside the bounding box and keeps a face only if the air it faces
is reachable from there, so faces sealing an enclosed hollow cost nothing.
**Greedy merging** then fuses adjacent same-hue faces on each plane into the
largest rectangles it can. The per-cell brightness survives because it is
evaluated in the fragment shader from world position, not baked into vertices —
one merged quad still draws one brightness square per voxel.

The same mesher feeds the city: `voxel.mesh_vertices` emits the exact vertex
layout `build.py` does, so a model concatenates onto a city's vertex buffer and
comes back lit by the city's sun and shadow map, still wearing its mosaic.

## Footprint shapes

A city repeats itself. `--build-footprints` reads every building in the extract,
reduces its footprint to a mask on the **voxel** grid, and writes the commonest
shapes out as one-layer voxel models — floor plans, ready to open in the editor
and build upwards into a house.

```
./env/bin/voxity --build-footprints
./env/bin/voxity --editor --model models/footprints/footprint-00-0-12x9.json
```

Each footprint is rotated onto its own walls first, so a house is one shape
however its street runs, and quarter turns and mirroring are folded together —
placing a model turned costs nothing. What is *not* folded away is size: a voxel
model is meshed at one cell size for the whole scene, so it can never be
rescaled when it is placed, and 8 × 12 m is a different footprint from
16 × 24 m.

That makes size an axis of its own, and the output is ranked on both. A
**family** is a shape with its size normalised away — every 2:1 rectangle is one
family, an L is another — and each family then writes out the few real sizes it
actually occurs at. Rank on shape alone and you get one rectangle where the city
has thirty; rank on size alone and all of it is rectangles, because that is what
a city is mostly made of.

Over Hamburg's 355k buildings that comes out as: ten families of plain rectangle
at proportions from 4:3 to 5:1, which between them are 70% of every building in
the city, and then L-shapes of varying notch, and a chamfered octagon. So
`--footprint-count` counts *shapes*, and you need to ask for more than ten of
them before anything stops being a box.

Shapes do not have to match exactly to be grouped: footprints within 88% overlap
of a family join it, and what gets written is the shape a **majority of the
family's members agree on** cell by cell, not any single building. So a ragged
survey of near-identical semi-detached houses comes out as one clean plan.

Everything here is measured against the **25 cm cell** (`voxel.CELL_M`), which
is the one place the size of a voxel is decided. Changing it invalidates the
survey, every model on disk and every cached city mesh, and means re-running
`--build-footprints` and `--build-houses` — the survey's cache key folds the
cell in, so it will not quietly reuse the old one.

Output lands in `models/footprints/`: a model per shape and size, an
`index.json` recording how many buildings each stands for, and a `sheet.png`
contact sheet to look at them all at once. It is work product, not a cache —
nothing deletes it, and a re-run overwrites the models in place, so move
anything you have started building out of there first.

The first run reads all 355k buildings and takes about **6 minutes**. The
survey is cached after that, so retuning how shapes are grouped re-runs in two
seconds — which is the point of caching the masks rather than the result.

## Houses in the city

`--build-houses` stands a few default houses on every footprint the survey
found, and the city then puts them back on the buildings they came from. They
are built to real proportions — a 3 m storey, a 1 x 1.4 m window every 2.4 m —
against the 25 cm cell, and written out as boxes rather than one row per voxel,
which is the difference between 20 kB a house and 180.

```
./env/bin/voxity --build-houses          # writes models/houses/
./env/bin/voxity --place wandsbek --size 800
```

The defaults are meant to be replaced. Each footprint gets five: one to five
storeys, cycling through a gabled, hipped and flat roof, with windows, a door
and a plinth in hues picked to be visible against the walls. Walls follow the
footprint's **perimeter**, so a courtyard gets walls round it too, and roofs are
built by repeatedly **shrinking the plan and stacking it** — which is why an L
keeps its inner corner instead of being given a rectangle's ridge. Every house
records the plan it stands on, so opening one in the editor gives you the ground
and the limits back. Filenames are only for reading by eye; the city reads the
footprint out of each file, so you can rename them, edit them, or draw your own
from scratch.

When a square loads, every building's footprint is measured the same way the
survey measured it and matched against the plans. A match gets one of that
plan's houses — turned and mirrored to fit, and chosen by which one is closest
to the building's real height, so a two-storey building does not get a
five-storey house. Which of the near-enough ones it gets is a hash of the OSM
id, so a given building always looks the same and the whole square stays
cacheable. **A building that matches nothing is extruded exactly as before**, and
so is anything no house is the right height for — that is what keeps a tower
block a tower block.

At the defaults about **one building in ten** gets a house, and it is the number
of footprints that limits it: a plan only matches at its own size, so
`--footprint-sizes 8` (120 plans instead of 32) takes it to roughly one in four,
at the cost of five times as many models to look after. `--no-voxel-houses`
extrudes everything, for an A/B.

Houses are baked into the cached square mesh along with everything else, and the
cache notices when you edit one — the key includes what is in `models/houses/`.

## The map

The overview is baked once per extract and cached in `cache/`, because it means
walking all 1.5M objects in the file — about 70 seconds for Hamburg. After that
it loads in a blink. It is a plain PNG, roughly 9 m per pixel, drawn from the
same OSM tags as the city but in a flat, light palette: water, parks and
farmland, building footprints as grey masses, and the road network from
motorway down to tertiary.

The map frames itself on the data rather than on the file's header box. Geofabrik's
Hamburg extract reaches 100 km west to take in the Neuwerk exclave, and honouring
that would put the city in a corner of an otherwise empty image.

The map is divided into a grid of equal squares and you pick one of them, rather
than placing a rectangle freehand. Every region is therefore the same size, and
any two are either identical or don't overlap at all — so a square you played
before is the same square when you come back to it, cache and all. `--size` sets
the edge length, and with it the grid: 1200 m gives Hamburg 31 × 30 cells.

| | |
|---|---|
| move mouse | highlight the cell under the cursor |
| arrows | move one cell (the view follows) |
| wheel, `+` `-` | zoom the map |
| right-drag | pan |
| click, `ENTER` | play the highlighted cell |
| `ESC` | back to the menu |

In the city, `M` takes you back to the map to pick somewhere else.

## The extract

`.osm.pbf` files are not in the repo — they are large and reproducible. The default
one is **fetched automatically on first run** (51 MB from
[Geofabrik](https://download.geofabrik.de/), with a progress line), so a fresh clone
just works:

```
./env/bin/voxity --place rathaus
fetching https://download.geofabrik.de/europe/germany/hamburg-260728.osm.pbf
     51.0 MB  100.0%
```

It streams through a `.part` file and renames only on success, so an interrupted
download never leaves a truncated extract behind. Once the file is there it is never
re-fetched.

For any other region, give both the file and where to get it — the region path can't
be guessed from a filename:

```
./env/bin/voxity --pbf berlin.osm.pbf \
    --pbf-url https://download.geofabrik.de/europe/germany/berlin-latest.osm.pbf
```

`--no-download` turns the fetch off and fails on a missing extract instead. Note the
built-in `--place` presets are all Hamburg.

## Controls

| | |
|---|---|
| `W A S D` / arrows | move (speed scales with altitude) |
| `Q` `E`, `ctrl` `space` | down / up |
| `shift` / `alt` | boost / crawl |
| drag mouse, or `TAB` | look around |
| wheel | movement speed |
| `,` `.` | time of day (sun height) |
| `[` `]` | sun azimuth |
| `T` `L` `G` `O` | trees, shadows, fog, ambient occlusion |
| `R` | back to the start position |
| `M` | back to the overview map |
| `P` | screenshot into `screenshots/` |
| `F1` | hide the key list |
| `ESC` | release the mouse, then back to the menu |

## Options

```
--pbf FILE        input extract        (default hamburg-260728.osm.pbf)
--pbf-url URL     where to fetch --pbf from when it is missing
--no-download     fail on a missing extract instead of downloading it
--place NAME      named preset         (--list-places to see them)
--center LAT,LON  centre of the square
--bbox W,S,E,N    explicit box in degrees
--size METRES     edge length of the square, and of the grid cells (default 1200)
--sun AZ,EL       sun azimuth/elevation in degrees (default 240,28)
--view YAW,PITCH,ALT   starting camera
--width --height  window size
--no-trees --no-shadows --no-cache
--no-ao           turn off ambient occlusion
--supersample F   render F times the window size and downsample (default 1.5)
--screenshot OUT.png   render one frame offscreen and exit
--frames N        quit after N frames (smoke test)
--build-map       bake the overview map offscreen and exit
--map-size PX     long edge of the baked map (default 4096)
--no-map          skip the picker and use the default square
--editor          skip the menu and open the voxel editor
--model FILE      model the editor opens   (default models/model.json)
--build-footprints     survey the extract's footprint shapes and exit
--footprint-cell M     voxel cell size for the footprints (default 1.0)
--footprint-count N    shape families to write (default 16)
--footprint-sizes N    real sizes of each shape to write (default 2)
--footprint-iou F      overlap at which two shapes are one family (default 0.88)
--footprint-dir DIR    where they go     (default models/footprints)
--build-houses         write default houses for every footprint and exit
--house-variants N     houses per footprint (default 5)
--house-dir DIR        where they live   (default models/houses)
--no-voxel-houses      extrude every building instead of placing houses
```

Naming a tool on the command line — `--place`/`--center`/`--bbox`, `--no-map`
or `--editor` — skips the menu, so every scripted invocation goes straight
where it always did.

`--screenshot` uses an offscreen EGL context, so it works without a display.
With `--editor` it renders the model instead of the city, which is the cheapest
check that the voxel shader still compiles.

## What gets built

* **Buildings** — extruded footprints, including multipolygons with
  courtyards. Height comes from `height`, else `building:levels`, else a
  guess per building type nudged by a hash of the id so blocks aren't flat.
  Walls and roofs are coloured from a north-German palette (brick, clinker,
  render, slate) unless the object carries `building:colour` / `roof:colour`.
  Small rectangular houses get a pitched roof. Buildings whose footprint is one
  of the surveyed floor plans get a **voxel house** instead — see above.
* **Roads** — mitred ribbons, width and shade by `highway` class, with a
  casing under the wider ones. Bridges are lifted by their `layer`, tunnels
  dropped.
* **Water** — areas and waterways, with a rippling specular in the shader.
* **Land cover** — parks, forest, sand, farmland, pitches, parking, industrial
  and residential landuse, stacked in layers so they don't z-fight.
* **Trees** — every `natural=tree` node, instanced, randomised in size. The
  canopy is a voxel blob rather than a cone, so foliage is built out of cubes
  like everything else.

Anything crossing the edge of the square is clipped to it; buildings that
straddle the edge are dropped rather than sliced.

## Layout

```
main.py               CLI, window, dispatch between the two tools
voxity/startscreen.py the menu

shared
voxity/mesh.py        the vertex layout and the triangle accumulator
voxity/shaders.py     GLSL, including the per-cell voxel hash
voxity/camera.py      matrices, the fly camera and the editor's orbit camera
voxity/hud.py         text overlay panels
voxity/ui.py          2D widgets: rectangles, text, menubar

city
voxity/extract.py     .osm.pbf -> features inside the box (cached)
voxity/tags.py        what OSM tags mean: width, height, colour
voxity/geo.py         local metric projection, clipping, ring/box helpers
voxity/build.py       features -> triangles (extrusion, ribbons, roofs, trees)
voxity/renderer.py    moderngl passes: shadow map, scene, sky, trees
voxity/overview.py    the whole extract baked into one flat 2D map (cached)
voxity/mapview.py     picking the region to play on, off that map
voxity/footprints.py  the extract's commonest footprints -> voxel floor plans
voxity/houses.py      a floor plan -> default voxel houses standing on it
voxity/place.py       a real building -> the house that matches it, placed

voxels
voxity/voxel.py       the model, the palette, the greedy mesher, JSON
voxity/editor/        the editing half: picking, GL, file dialogs, the loop
```

`voxel.py` sits deliberately below both: it holds everything the editor and the
city have to agree on, and touches neither GL nor pygame.

Coordinates are projected to metres around the centre of the square:
x east, z south, y up. The editor works in unit cells with the same axes.

## The look

The city is graded for bright daylight. Everything is drawn into an
offscreen buffer in linear light and only turned into a picture at the end,
which is what lets **ambient occlusion** darken the light itself rather than the
image of it — so walls meet the ground in a soft shadow, eaves and voxel steps
get a crevice, and the whole thing stops looking like flat-shaded cardboard.
After that comes a filmic curve, a warm/cool split tone and a vignette.

Antialiasing is supersampling: the scene is rendered half again as large as the
window and scaled down. That is not just for edges — the voxel brightness mosaic
is computed inside triangles, where multisampling can do nothing at all. It
costs roughly a third of the frame rate, and `--supersample 1` turns it off.
Ambient occlusion is free by comparison; `--no-ao` and the `O` key are there to
see what it is doing, not to speed anything up.

The default sun is high and near-white. Winding its elevation down with `,`
warms the whole palette towards a golden hour rather than merely dimming it, so
`--sun 240,20` is a sunset and `--sun 215,52` is the daylight the look is built
around. `[` `]` swing it round.

## Caching

The first run for a box reads the whole `.pbf` (~15 s for Hamburg). Both the
extracted features and the built mesh land in `cache/`, so the same square
opens in a couple of seconds afterwards. The overview map is cached there too,
and costs about 70 s the first time; the footprint survey is cached there too and
costs 4-5 minutes. `--no-cache` skips all four — which means re-baking the map
and re-reading every building as well, so it is a slow flag. Deleting `cache/` is always safe; note that the
footprint and house *models* are not in there, they are work product under
`models/footprints/` and `models/houses/`. Editing one of those does invalidate
the cached square meshes built with it — the mesh key includes what is in the
house directory — so a change shows up on the next run without a `--no-cache`.

## Setup

`./env` is a **conda environment** with Poetry installed inside it, and the project
installed editable into that same environment:

```
conda create -p ./env python=3.12 -y
conda install -p ./env -c conda-forge poetry -y
./env/bin/poetry install
ln -sf /usr/lib64/libGL.so.1  env/lib/libGL.so      # see "OpenGL libraries" below
ln -sf /usr/lib64/libEGL.so.1 env/lib/libEGL.so
```

Conda provides the interpreter and Poetry; Poetry reads `pyproject.toml` /
`poetry.lock` and pip-installs the project dependencies plus the project itself
into `./env`. `poetry.toml` sets `virtualenvs.create = false` so Poetry installs
into the interpreter it is running under instead of making a virtualenv.

The project goes in editable — `voxity.pth` puts the repo root on `sys.path`, so
edits to `voxity/` and `main.py` take effect with no reinstall. Only a change to
`pyproject.toml` needs `./env/bin/poetry install` re-run.

Run everything through `./env/bin/`:

```
./env/bin/voxity --place rathaus       # or ./env/bin/python main.py ...
./env/bin/poetry add somepackage       # updates pyproject + lock, installs into ./env
./env/bin/ruff check .
```

### OpenGL libraries

moderngl's `create_context()` (the windowed path) `dlopen`s the *unversioned*
`libEGL.so` / `libGL.so`. Fedora-family systems — Bazzite and other rpm-ostree images
included — ship only the versioned runtime `libEGL.so.1` / `libGL.so.1`; the
unversioned names come from `-devel` packages that aren't installed. Without them
`main.py` dies with `OSError: libGL.so: cannot open shared object file`, *after*
extracting and meshing, at `moderngl.create_context()`.

The two symlinks above supply the missing names from `env/lib`, which is already on
the interpreter's `dlopen` search path (conda's `RUNPATH`), so they work both under
`conda activate ./env` and via `./env/bin/...`, with no `LD_LIBRARY_PATH`.

They deliberately point at the **system** libraries: pygame/SDL creates the GL context
against the system driver, so moderngl has to load that same one. Installing conda-forge
`mesa`/`libglvnd` into `./env` would put a real `libGL.so.1` in `env/lib` that shadows
the system driver — expect a broken or software-only context. Don't.

This is unrelated to conda; a plain venv fails identically. `--screenshot` is unaffected,
because it goes through a standalone EGL context that loads the versioned name.

Two things to be careful about:

* **Always use `./env/bin/poetry`.** Because `virtualenvs.create = false`, a Poetry
  from anywhere else run in this directory would resolve "the active interpreter" to
  something that is not `./env` — possibly your conda base.
* Poetry installs with pip, so a few packages conda put in `./env` (`numpy`,
  `requests`, `urllib3`, `certifi`, `idna`, `charset-normalizer`) are now pip-owned
  and show as `pypi` in `conda list`. Don't `conda update` those — let Poetry and the
  lock own them. Use conda only for the interpreter, Poetry, and any non-Python
  system libraries.
