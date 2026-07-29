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

```
./env/bin/voxity                                  # menu
./env/bin/voxity --place rathaus --size 1200      # straight into the city
./env/bin/voxity --center 53.5503,9.9937 --size 2000
./env/bin/voxity --bbox 9.98,53.54,10.00,53.56
./env/bin/voxity --editor                         # straight into the editor
```

## The editor

Unit cubes on a grid, an orbiting camera, and a brush that stamps 1, 8 or 64 of
them at a time. You pick the **hue**; the brightness is not yours to choose —
it is a hash of the cell's position, so neighbouring voxels vary slightly and a
given cell always looks the same. That is what gives a flat wall its mosaic.

| | |
|---|---|
| left-drag | orbit |
| right-drag | pan |
| wheel | zoom |
| left-click | add a block on the green highlight |
| right-click | delete the voxel under the red highlight |
| click a swatch, `1`–`9` `0` | set the hue |
| `,` `.` | shrink / grow the brush (1 / 8 / 64 voxels) |
| `G` | floor grid |
| `F` | frame the model |
| `S` `L` | save / load the current model |
| `ESC` | back to the menu |

**File** does New / Open / Save / Save As / Export OBJ / Export PNG through a
native dialog when tkinter is available. Models are JSON under `models/`.

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
| `T` `L` `G` | trees, shadows, fog |
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
--sun AZ,EL       sun azimuth/elevation in degrees (default 235,34)
--view YAW,PITCH,ALT   starting camera
--width --height  window size
--no-trees --no-shadows --no-cache
--screenshot OUT.png   render one frame offscreen and exit
--frames N        quit after N frames (smoke test)
--build-map       bake the overview map offscreen and exit
--map-size PX     long edge of the baked map (default 4096)
--no-map          skip the picker and use the default square
--editor          skip the menu and open the voxel editor
--model FILE      model the editor opens   (default models/model.json)
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
  Small rectangular houses get a pitched roof.
* **Roads** — mitred ribbons, width and shade by `highway` class, with a
  casing under the wider ones. Bridges are lifted by their `layer`, tunnels
  dropped.
* **Water** — areas and waterways, with a rippling specular in the shader.
* **Land cover** — parks, forest, sand, farmland, pitches, parking, industrial
  and residential landuse, stacked in layers so they don't z-fight.
* **Trees** — every `natural=tree` node, instanced, randomised in size.

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
voxity/geo.py         local metric projection, polygon/line clipping
voxity/build.py       features -> triangles (extrusion, ribbons, roofs, trees)
voxity/renderer.py    moderngl passes: shadow map, scene, sky, trees
voxity/overview.py    the whole extract baked into one flat 2D map (cached)
voxity/mapview.py     picking the region to play on, off that map

voxels
voxity/voxel.py       the model, the palette, the greedy mesher, JSON
voxity/editor/        the editing half: picking, GL, file dialogs, the loop
```

`voxel.py` sits deliberately below both: it holds everything the editor and the
city have to agree on, and touches neither GL nor pygame.

Coordinates are projected to metres around the centre of the square:
x east, z south, y up. The editor works in unit cells with the same axes.

## Caching

The first run for a box reads the whole `.pbf` (~15 s for Hamburg). Both the
extracted features and the built mesh land in `cache/`, so the same square
opens in a couple of seconds afterwards. The overview map is cached there too,
and costs about 70 s the first time. `--no-cache` skips all three — which now
means re-baking the map as well, so it is a slow flag. Deleting `cache/` is
always safe.

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
