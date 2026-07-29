# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-binary-ish Python app that reads a square out of an `.osm.pbf` extract, turns
OSM features into triangles, and flies a camera through them with moderngl. Launched
without a region it first shows a flat 2D map of the whole extract and lets you pick the
square to play on. No test suite — just `main.py` and the `voxity/` package, Poetry for
dependencies, and git (`origin` is `github.com:christopher-schroeder/voxity`).

`.osm.pbf` extracts are **not** in the repo (`.gitignore`), nor is `cache/` or `env/`.
`main.ensure_pbf` fetches the default extract from Geofabrik on first run, so a fresh
clone works unattended; `DEFAULT_PBF` and `PBF_URL` must stay in step, since the URL is
built from the filename. Any other `--pbf` needs an explicit `--pbf-url`.

## Running

**`./env` is gitignored, so a fresh clone does not have one** and every command below
fails with "No such file or directory" until it is built. The README's Setup block is
the source of truth; the short form is `conda create -p ./env python=3.12`,
`conda install -p ./env -c conda-forge poetry`, `./env/bin/poetry install`. Check with
`ls -d env` before concluding anything else is broken.

The environment is `./env`, a **conda environment** with Poetry installed inside it and
the project installed **editable** into that same environment:

```
./env/bin/voxity                                # map first, then pick a square
./env/bin/voxity --place rathaus --size 1200    # straight into the city
./env/bin/voxity --center 53.5503,9.9937 --size 2000
./env/bin/voxity --no-map                       # skip the picker, default square
./env/bin/voxity --list-places                  # 14 Hamburg presets in main.py
```

**A region argument suppresses the map.** `--place`/`--center`/`--bbox` (and
`--screenshot`, which has nobody to click) go straight to the old direct path, so every
scripted invocation behaves exactly as it did before the picker existed. Only the
bare-argument case changed, and it used to default to `rathaus`.

`voxity` is a console script pointing at `main:main`; `./env/bin/python main.py ...`
is equivalent. The **system** `python3` does *not* have moderngl — always go through
`./env/bin/`.

Because the install is editable (`voxity.pth` puts the repo root on `sys.path`), edits
to `voxity/` and `main.py` take effect immediately — never reinstall to test a change.
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

**The windowed path may need two GL symlinks that are not in any manifest — check the
host first.** `moderngl.create_context()` `dlopen`s the *unversioned* `libEGL.so` /
`libGL.so`. Whether those exist is a property of the machine, not of this project:

```
ls /usr/lib*/libGL.so /usr/lib*/libEGL.so   # present on Arch/Manjaro (mesa ships them)
```

If they are there, nothing is needed. Fedora-family hosts (Bazzite and other rpm-ostree
images included) ship only the versioned `.so.1` — the unversioned names come from
`-devel` packages — and the run then dies at `create_context()` *after* extract and mesh,
which makes it look like a renderer bug. The fix is `env/lib/libGL.so` and
`env/lib/libEGL.so` symlinked to whatever the host's real `.so.1` files are (`/usr/lib`
on Arch, `/usr/lib64` on Fedora — the README's paths are Fedora's). `env/lib` is already
on the dlopen path via conda's `RUNPATH`, so no `LD_LIBRARY_PATH` is involved, and
recreating `./env` drops them. They must point at the **system** libs, since SDL makes
the context against the system driver; conda-forge `mesa`/`libglvnd` in `./env` would
shadow it. `--screenshot` never hits this, so headless smoke tests pass while the window
is broken — check both.

`main.py` is a top-level module, not part of the `voxity` package, so `pyproject.toml`
lists it explicitly under `[tool.poetry] packages` alongside `voxity`. A new top-level
module would need the same treatment.

### Smoke tests

There are no unit tests. Three ways to verify a change without a human at the keyboard,
cheapest first:

```
# 1. pipeline only, no GL at all — the right check for tags/extract/build edits
./env/bin/python -c "from voxity import extract; from voxity.build import build_scene; \
from voxity.geo import square_bbox; \
print(build_scene(extract.load('hamburg-260728.osm.pbf', \
square_bbox(9.9937, 53.5503, 600)))[0].shape)"

# 2. one frame through a standalone EGL context, headless
./env/bin/voxity --place rathaus --size 600 --screenshot out.png

# 3. the real window, quits by itself
./env/bin/voxity --place rathaus --size 600 --frames 3

# 4. bake the overview map headlessly (EGL, no display needed)
./env/bin/voxity --build-map --map-size 512

# 5. the map-first startup: picker for 3 frames, then quits
./env/bin/voxity --frames 3
```

(1) works because `main.run` and `main.render_headless` import moderngl and pygame
*inside* the function — extract and build never touch GL, so this still passes when the
GL symlinks above are missing, which is what distinguishes a mesh bug from a context
bug. (2) is the only check that exercises the shaders without a display; note it renders
with `dt=0`, so `u_time` stays 0 and the water ripple is frozen, and it ignores
`--frames`. `--no-cache` forces a full re-extract (~15 s for the Hamburg pbf) when you
need to test the cold path. (4) is the way to exercise overview.py headlessly, but note a
cold bake costs ~55 s **whatever `--map-size` you give**: the time goes on the osmium pass
and triangulating 400k areas, not on rasterising, so a smaller map buys you a smaller file
and nothing else. Budget for it rather than assuming a low resolution is quick. (5) covers the
picker but *not* the selection, since nothing clicks: to test map → play end to end you
have to `pygame.event.post` a `MOUSEBUTTONDOWN` before calling `mapview.choose_region`,
then drive `main.fly` the same way. Worth doing — it is the only check that the uv →
lon/lat → bbox chain comes out the right size and in the right hemisphere.

## Pipeline

There are **two** pipelines over the same `.osm.pbf`, and they share only tags.py and
geo.py:

```
play:  .osm.pbf → extract.py → Scene → build.py → (verts, trees) → renderer.py → GL
map:   .osm.pbf → overview.py ─────────────────────────────────→ PNG → mapview.py → GL
```

The play pipeline is the original one: a 1 km-ish square, extruded and lit. Each stage's
output is the next stage's only input; `Scene` (extract.py) is the seam. Everything in a
`Scene` is already projected to metres and clipped — build.py never sees lon/lat, and
renderer.py never sees OSM tags.

The map pipeline covers the *whole extract* at once and deliberately does **not** build a
`Scene`: 400k areas as Python dicts is the thing to avoid. overview.py streams triangles
straight into a GPU buffer and flushes when it fills, so peak memory is flat in the size
of the extract. Its output is a flat PNG, and mapview.py only ever sees that image.

- **voxity/tags.py** — the whole tag vocabulary lives here as module-level tables
  (`ROADS`, `RAILS`, `WATERWAYS`, `SURFACES`, `_SURFACE_RULES`, palettes). Pure functions
  over tag dicts, no numpy, no OSM objects. Add or retune feature types here first.
- **voxity/extract.py** — one `osmium.FileProcessor` pass with C++-side key filters,
  plus Python-side coarse rejection (`_coarse_reject_*`, sampling 2–3 nodes against a
  padded bbox) because materialising every ring of a city extract is the bottleneck.
  `KEYS` selects which **objects** survive the filter — it does not strip tags, so a
  kept object arrives with its full tag dict.
- **voxity/geo.py** — `Projection` (equirectangular around the box centre) and the
  Sutherland-Hodgman / Liang-Barsky clippers.
- **voxity/build.py** — `MeshBuilder` accumulates triangle soup; `build_scene` draws
  ground skirt → surfaces (by layer) → lines (by elev, layer) → buildings.
- **voxity/renderer.py** + **shaders.py** — shadow pass, scene pass, instanced trees,
  fullscreen sky. GLSL lives as strings in shaders.py, sharing a `COMMON_LIGHTING` chunk.
- **voxity/camera.py** — matrix helpers (`perspective`, `ortho`, `look_at`, `to_gl`) and
  the fly camera. **voxity/hud.py** — pygame-rendered text uploaded as a GL texture.
- **voxity/overview.py** — bakes the whole extract into one flat PNG. Two passes:
  `data_bbox` histograms one node per object to find where the data actually *is*, then
  `bake` streams triangles through `_Batch`. Needs a live GL context, so it is imported
  lazily; `--build-map` bakes it headlessly through the same EGL path as `--screenshot`.
- **voxity/mapview.py** — the region picker. Owns its own event loop, returns a lon/lat
  box, and knows nothing about how the map was made.

## Conventions that cut across files

**Map orientation is a third coordinate convention, and it flips three times.**
overview.py bakes with a *negative* y scale so north (−z) lands at the top of the
framebuffer, then `fbo.read` hands back rows bottom-up, so `OverviewMap.pixels` is
GL-order (row 0 = south). mapview.py reasons in **top-down uv** — (0,0) is north-west,
which is what `uv_to_lonlat` expects, and what pygame's mouse y already agrees with. So
the picker's two shaders each flip once: `MAPVIEW_VS` flips uv y into clip space (where
+1 is up), and `MAPVIEW_FS` flips `v` again on the texture lookup, because the texture is
stored bottom-up. Miss the vertex-stage flip and *both* the map and the selection square
are mirrored north-south — the square visibly chases the cursor the wrong way, but the
map alone looks plausible until you notice the Elbe is on the wrong side.

The invariant to test is that the pixel drawn at a screen position is the map pixel
`MapView.screen_to_uv` says is there. Sampling the framebuffer against
`OverviewMap.pixels` at a few zoom levels catches every sign error in the chain at once,
which eyeballing the map does not.

**Coordinates.** `Projection.forward` gives x = east, z = **south** (north is −z), y = up.
Every `Scene` array is `(x, z)` 2D; build.py lifts it to `(x, y, z)`. Camera yaw 0 looks
north. Get this backwards and the city mirrors.

**Vertex layout.** `3f 3f 3f 1f` = position, normal, colour, material — declared in
`MeshBuilder.pack`, consumed by `Renderer.scene_vao`, read by `SCENE_VS`. Material 0 is
matte, 1 is water (ripple normal + specular in `SCENE_FS`); those are the only two, and
`SCENE_FS` branches on `v_mat > 0.5`, so any third material added later renders as water
until that test is replaced. Trees are a separate program and buffer entirely
(`tree_mesh` + per-instance `x, z, height, radius, tint`), so the material slot never
sees foliage. Changing the layout means touching all three places,
plus the `'3f 28x'` stride in `scene_depth_vao`.

**Three cache versions, and they are not symmetric.** `extract.CACHE_VERSION` keys the
pickled `Scene`; `main.MESH_VERSION` keys the `.npz` mesh; `overview.MAP_VERSION` keys
the baked map PNG, which is on its own branch entirely — it is *not* affected by
`CACHE_VERSION`, because overview.py never builds a `Scene`. Bump `MAP_VERSION` when
overview.py or the `MAP_*` tables in tags.py change what the map looks like; a stale map
is the one cache whose staleness you can actually see. Bump `CACHE_VERSION` when
extraction or `tags.py` changes what lands in a `Scene`; bump `MESH_VERSION` when
`build.py` changes geometry. The mesh filename is built from `extract.cache_key(...)`
*plus* `MESH_VERSION`, and `cache_key` already folds in `CACHE_VERSION` — so bumping
`CACHE_VERSION` invalidates **both** caches, while `MESH_VERSION` invalidates only the
`.npz`. `cache_key` also folds in the pbf's size and mtime, so a re-downloaded extract
invalidates everything on its own. Forgetting a bump means a stale `cache/*.pkl` or
`cache/*.npz` silently masks your edit — if a change "does nothing", suspect this first.
Deleting `cache/` is always safe.

`--no-trees` is applied *after* the mesh cache is read (`main.prepare` slices `trees` to
length zero), so trees are always meshed and stored: changing tree geometry still needs a
`MESH_VERSION` bump, but toggling the flag never does.

The map key also folds in the pixel long edge, so `--map-size` gets its own file rather
than silently reusing a differently-sized bake. Re-baking costs about 70 s for Hamburg at
4096 (10 s to find the data bbox, 60 s for the main pass) and barely less at 512, since
the cost is per-feature and not per-pixel. That is why `--no-cache` is expensive on the
map path in a way it never was before.

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
   including its layer and colour. If it should also show on the overview map, give it a
   `MAP_SURFACES` / `MAP_ROADS` entry — the map reuses `surface_class` to decide *what* a
   feature is and only overrides the colour, so a new surface class with no `MAP_SURFACES`
   row is silently invisible on the map.
2. Only if the feature is *identified* by a key no kept object would already carry, add
   that key to `extract.KEYS` — the osmium `KeyFilter` drops objects carrying none of
   them in C++ before Python sees it. Tags read as *attributes* of an already-kept object
   need no change: `tunnel`, `bridge`, `layer`, `height`, `building:levels`, `lanes`,
   `width` and the colour tags are all absent from `KEYS` and work fine.
3. Route it in `extract._area` or `extract._way` if it isn't already covered by
   `surface_class` / the `ROADS`-`RAILS`-`WATERWAYS` lookup.
4. Bump `CACHE_VERSION` (and `MESH_VERSION` if build.py changed, and `MAP_VERSION` if it
   shows on the map), then verify with `--screenshot` and, if the map changed,
   `--build-map --map-size 512`.

## Style

Ruff is configured in `pyproject.toml` (line-length 95, single quotes) and installed in
`env`: `./env/bin/ruff check .`. It currently reports 3 pre-existing findings —
two `BLE001` blind `except Exception` around osmium ring materialisation (deliberate; two
sibling sites already carry `# noqa: BLE001`) and one `B905` `zip()` without `strict=`.

Existing code is plain functions and dicts, single quotes, numpy-vectorised where it
matters, with comments that explain *why* an odd thing is there (coarse rejection, ring
re-winding, part insetting). Match that: prefer array ops over per-feature Python loops in
extract/build — those run over hundreds of thousands of features.
