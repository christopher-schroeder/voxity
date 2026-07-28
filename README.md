# osm city

Builds a 3D city out of an `.osm.pbf` extract and flies you through it.
pygame for the window and input, moderngl for the rendering.

Give it a coordinate square; it pulls out everything inside, extrudes the
buildings, lays out the roads and water, and renders the result with a
shadow-mapped sun.

```
./env/bin/osm-city --place rathaus --size 1200
./env/bin/osm-city --center 53.5503,9.9937 --size 2000
./env/bin/osm-city --bbox 9.98,53.54,10.00,53.56
```

## The extract

`.osm.pbf` files are not in the repo — they are large and reproducible. The default
one is **fetched automatically on first run** (51 MB from
[Geofabrik](https://download.geofabrik.de/), with a progress line), so a fresh clone
just works:

```
./env/bin/osm-city --place rathaus
fetching https://download.geofabrik.de/europe/germany/hamburg-260728.osm.pbf
     51.0 MB  100.0%
```

It streams through a `.part` file and renames only on success, so an interrupted
download never leaves a truncated extract behind. Once the file is there it is never
re-fetched.

For any other region, give both the file and where to get it — the region path can't
be guessed from a filename:

```
./env/bin/osm-city --pbf berlin.osm.pbf \
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
main.py             CLI, window, event loop
osmcity/extract.py  .osm.pbf -> features inside the box (cached)
osmcity/tags.py     what OSM tags mean: width, height, colour
osmcity/geo.py      local metric projection, polygon/line clipping
osmcity/build.py    features -> triangles (extrusion, ribbons, roofs, trees)
osmcity/renderer.py moderngl passes: shadow map, scene, sky, trees
osmcity/shaders.py  GLSL
osmcity/camera.py   matrices and the fly camera
osmcity/hud.py      text overlay
```

Coordinates are projected to metres around the centre of the square:
x east, z south, y up.

## Caching

The first run for a box reads the whole `.pbf` (~15 s for Hamburg). Both the
extracted features and the built mesh land in `cache/`, so the same square
opens in a couple of seconds afterwards. `--no-cache` skips it; deleting
`cache/` is always safe.

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

The project goes in editable — `osm_city.pth` puts the repo root on `sys.path`, so
edits to `osmcity/` and `main.py` take effect with no reinstall. Only a change to
`pyproject.toml` needs `./env/bin/poetry install` re-run.

Run everything through `./env/bin/`:

```
./env/bin/osm-city --place rathaus     # or ./env/bin/python main.py ...
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
