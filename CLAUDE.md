# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-binary-ish Python app that reads a square out of an `.osm.pbf` extract, turns
OSM features into triangles, and flies a camera through them with moderngl. No test
suite — just `main.py` and the `osmcity/` package, Poetry for dependencies, and git
(`origin` is `github.com:christopher-schroeder/voxity`).

`.osm.pbf` extracts are **not** in the repo (`.gitignore`), nor is `cache/` or `env/`.
`main.ensure_pbf` fetches the default extract from Geofabrik on first run, so a fresh
clone works unattended; `DEFAULT_PBF` and `PBF_URL` must stay in step, since the URL is
built from the filename. Any other `--pbf` needs an explicit `--pbf-url`.

## Running

The environment is `./env`, a **conda environment** with Poetry installed inside it and
the project installed **editable** into that same environment:

```
./env/bin/osm-city --place rathaus --size 1200    # window, fly around
./env/bin/osm-city --center 53.5503,9.9937 --size 2000
./env/bin/osm-city --list-places                  # 14 Hamburg presets in main.py
```

`osm-city` is a console script pointing at `main:main`; `./env/bin/python main.py ...`
is equivalent. The **system** `python3` does *not* have moderngl — always go through
`./env/bin/`.

Because the install is editable (`osm_city.pth` puts the repo root on `sys.path`), edits
to `osmcity/` and `main.py` take effect immediately — never reinstall to test a change.
Only a change to `pyproject.toml` itself needs `./env/bin/pip install -e .` re-run.

**Poetry lives inside `./env` and installs into it.** `poetry.toml` sets
`virtualenvs.create = false`, so Poetry installs into the interpreter it is running
under. That only lands in `./env` when Poetry *is* `./env/bin/poetry` — always spell it
out. Poetry 2.x cannot be pointed at `./env` any other way: it ignores both
`env use <path>` and an activated `VIRTUAL_ENV` and mints its own virtualenv instead, and
`create = false` with any other Poetry would silently target the conda base environment.

**Conda owns the interpreter, Poetry owns the packages.** Poetry installs with pip, so
`numpy`, `requests`, `urllib3`, `certifi`, `idna` and `charset-normalizer` are pip-owned
inside a conda env and list as `pypi` in `conda list`. Don't `conda update` them —
`poetry.lock` is the source of truth (it holds numpy at 2.2.6 where conda shipped 2.5.1).
Reserve conda for the interpreter, Poetry itself, and non-Python system libraries.

Because `./env` is a conda env it has **no `pyvenv.cfg`**, which is what Ruff's automatic
virtualenv detection looks for — hence `extend-exclude = ["env"]` in `pyproject.toml`.
Drop it and `ruff check .` lints the entire environment (~17k findings). Any other tool
that walks the tree needs the same treatment.

**The windowed path needs two GL symlinks that are not in any manifest.**
`moderngl.create_context()` `dlopen`s the unversioned `libEGL.so`/`libGL.so`; this
Fedora/Bazzite host has only the versioned `.so.1` (no `-devel` packages), so the run
dies at `create_context()` — *after* extract and mesh, which makes it look like a
renderer bug. `env/lib/libGL.so` and `env/lib/libEGL.so` symlink the system libraries
in; `env/lib` is already on the dlopen path via conda's `RUNPATH`, so no
`LD_LIBRARY_PATH` is involved. Recreating `./env` drops them — re-add them (see README).
They must point at the **system** libs, since SDL makes the context against the system
driver; conda-forge `mesa`/`libglvnd` in `./env` would shadow it. `--screenshot` never
hits this, so headless smoke tests pass while the window is broken — check both.

`main.py` is a top-level module, not part of the `osmcity` package, so `pyproject.toml`
lists it explicitly under `[tool.poetry] packages` alongside `osmcity`. A new top-level
module would need the same treatment.

### Smoke tests

There are no unit tests. The two ways to verify a change without a human at the keyboard:

```
./env/bin/osm-city --place rathaus --size 600 --frames 3      # window, quits after 3 frames
./env/bin/osm-city --place rathaus --size 600 --screenshot out.png
```

`--screenshot` renders one frame through a standalone EGL context, so it works headless;
it is the only check that exercises the shaders without a display. `--no-cache` forces a
full re-extract (~15 s for the Hamburg pbf) when you need to test the cold path.

## Pipeline

```
.osm.pbf → extract.py → Scene → build.py → (verts, tree instances) → renderer.py → GL
```

Each stage's output is the next stage's only input; `Scene` (extract.py) is the seam.
Everything in a `Scene` is already projected to metres and clipped — build.py never sees
lon/lat, and renderer.py never sees OSM tags.

- **osmcity/tags.py** — the whole tag vocabulary lives here as module-level tables
  (`ROADS`, `RAILS`, `WATERWAYS`, `SURFACES`, `_SURFACE_RULES`, palettes). Pure functions
  over tag dicts, no numpy, no OSM objects. Add or retune feature types here first.
- **osmcity/extract.py** — one `osmium.FileProcessor` pass with C++-side key filters,
  plus Python-side coarse rejection (`_coarse_reject_*`, sampling 2–3 nodes against a
  padded bbox) because materialising every ring of a city extract is the bottleneck.
- **osmcity/geo.py** — `Projection` (equirectangular around the box centre) and the
  Sutherland-Hodgman / Liang-Barsky clippers.
- **osmcity/build.py** — `MeshBuilder` accumulates triangle soup; `build_scene` draws
  ground skirt → surfaces (by layer) → lines (by elev, layer) → buildings.
- **osmcity/renderer.py** + **shaders.py** — shadow pass, scene pass, instanced trees,
  fullscreen sky. GLSL lives as strings in shaders.py, sharing a `COMMON_LIGHTING` chunk.
- **osmcity/camera.py** — matrix helpers (`perspective`, `ortho`, `look_at`, `to_gl`) and
  the fly camera. **osmcity/hud.py** — pygame-rendered text uploaded as a GL texture.

## Conventions that cut across files

**Coordinates.** `Projection.forward` gives x = east, z = **south** (north is −z), y = up.
Every `Scene` array is `(x, z)` 2D; build.py lifts it to `(x, y, z)`. Camera yaw 0 looks
north. Get this backwards and the city mirrors.

**Vertex layout.** `3f 3f 3f 1f` = position, normal, colour, material — declared in
`MeshBuilder.pack`, consumed by `Renderer.scene_vao`, read by `SCENE_VS`. Material 0 is
matte, 1 is water (ripple normal + specular in `SCENE_FS`). Trees are a separate program
and buffer entirely (`tree_mesh` + per-instance `x, z, height, radius, tint`), so the
material slot never sees foliage. Changing the layout means touching all three places,
plus the `'3f 28x'` stride in `scene_depth_vao`.

**Two cache versions, and they are separate.** `extract.CACHE_VERSION` keys the pickled
`Scene`; `main.MESH_VERSION` keys the `.npz` mesh. Bump `CACHE_VERSION` when extraction
or `tags.py` changes what lands in a `Scene`; bump `MESH_VERSION` when `build.py` changes
geometry. Forgetting means stale `cache/*.pkl` or `cache/*.npz` silently masks your edit —
if a change "does nothing", suspect this first. Deleting `cache/` is always safe.

**Flat geometry is stacked, not depth-tested apart.** Ground layers sit at
`layer * LAYER_STEP` (0.15 m); the `layer` number comes from the `ROADS`/`SURFACES` tables
and road casings drop by `CASING_DROP`. Bridges add `structure_height` on top. A new
ground feature needs a layer number that doesn't collide with an existing one at the same
place, or it z-fights.

**Winding.** Back-face culling is on. OSM does not guarantee ring direction, so
`_add_building` normalises winding per ring (outer CCW, holes CW) before extruding, and
`MeshBuilder.add` re-winds every triangle to agree with the normal it was given. Pass the
normal you want; don't hand-order vertices.

**Determinism.** Unmapped heights, colours, roof shapes and per-building tint all come
from `_hash01`/`_jitter` seeded on the OSM id with a per-purpose salt — so the same box
looks identical run to run, and cached meshes stay valid. Never swap these for `random`.

**Shadow map is cached** on `(sun azimuth, elevation, show_trees)`; it only re-renders
when the sun moves. Anything that changes scene geometry at runtime would have to
invalidate `Renderer._shadow_key`.

## Adding a feature type

1. Add the tag → value entry in `tags.py` (`ROADS`/`SURFACES` + `_SURFACE_RULES`/etc.),
   including its layer and colour.
2. If it needs a tag key not already in `extract.KEYS`, add it there — the osmium filter
   drops everything else in C++ before Python sees it.
3. Route it in `extract._area` or `extract._way` if it isn't already covered by
   `surface_class` / the `ROADS`-`RAILS`-`WATERWAYS` lookup.
4. Bump `CACHE_VERSION` (and `MESH_VERSION` if build.py changed), then verify with
   `--screenshot`.

## Style

Ruff is configured in `pyproject.toml` (line-length 95, single quotes) and installed in
`env`: `./env/bin/ruff check .`. It currently reports 3 pre-existing findings —
two `BLE001` blind `except Exception` around osmium ring materialisation (deliberate; two
sibling sites already carry `# noqa: BLE001`) and one `B905` `zip()` without `strict=`.

Existing code is plain functions and dicts, single quotes, numpy-vectorised where it
matters, with comments that explain *why* an odd thing is there (coarse rejection, ring
re-winding, part insetting). Match that: prefer array ops over per-feature Python loops in
extract/build — those run over hundreds of thousands of features.
