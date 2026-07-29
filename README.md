# voxity

Builds a 3D city out of an `.osm.pbf` extract and flies you through it.
pygame for the window and input, moderngl for the rendering.

Start it with no arguments and you get a flat map of the whole extract — the
city at a glance, roads and water and parks — to pick your square off. Pick one
and it pulls out everything inside, extrudes the buildings, lays out the roads
and water, and renders the result with a shadow-mapped sun.

```
./env/bin/voxity                                  # map, then pick a square
./env/bin/voxity --place rathaus --size 1200      # straight in
./env/bin/voxity --center 53.5503,9.9937 --size 2000
./env/bin/voxity --bbox 9.98,53.54,10.00,53.56
```

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

| | |
|---|---|
| move mouse | place the square |
| wheel | square size (200 m – 8 km) |
| `ctrl`+wheel, `+` `-` | zoom the map |
| right-drag, arrows | pan |
| click, `ENTER` | play here |
| `ESC` | quit |

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
| `ESC` | release the mouse, then quit |

## Options

```
--pbf FILE        input extract        (default hamburg-260728.osm.pbf)
--pbf-url URL     where to fetch --pbf from when it is missing
--no-download     fail on a missing extract instead of downloading it
--place NAME      named preset         (--list-places to see them)
--center LAT,LON  centre of the square
--bbox W,S,E,N    explicit box in degrees
--size METRES     edge length of the square (default 1200)
--sun AZ,EL       sun azimuth/elevation in degrees (default 235,34)
--view YAW,PITCH,ALT   starting camera
--width --height  window size
--no-trees --no-shadows --no-cache
--screenshot OUT.png   render one frame offscreen and exit
--frames N        quit after N frames (smoke test)
--build-map       bake the overview map offscreen and exit
--map-size PX     long edge of the baked map (default 4096)
--no-map          skip the picker and use the default square
```

`--screenshot` uses an offscreen EGL context, so it works without a display.

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
main.py            CLI, window, event loop
voxity/extract.py  .osm.pbf -> features inside the box (cached)
voxity/tags.py     what OSM tags mean: width, height, colour
voxity/geo.py      local metric projection, polygon/line clipping
voxity/build.py    features -> triangles (extrusion, ribbons, roofs, trees)
voxity/renderer.py moderngl passes: shadow map, scene, sky, trees
voxity/overview.py the whole extract baked into one flat 2D map (cached)
voxity/mapview.py  picking the region to play on, off that map
voxity/shaders.py  GLSL
voxity/camera.py   matrices and the fly camera
voxity/hud.py      text overlay
```

Coordinates are projected to metres around the centre of the square:
x east, z south, y up.

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
