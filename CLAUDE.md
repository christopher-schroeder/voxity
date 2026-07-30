# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-binary-ish Python app with **two tools sharing one window and one GL context**:
a city that reads a square out of an `.osm.pbf` extract, turns OSM features into
triangles and flies a camera through them; and a **voxel editor** for the models the
city is made of. Launched bare it shows a start screen and you pick one. The city path
then shows a flat 2D map of the whole extract to pick the square to play on. No test
suite — just `main.py` and the `voxity/` package, Poetry for dependencies, and git
(`origin` is `github.com:christopher-schroeder/voxity`).

The editor was a separate project (`voxelforge`, PyOpenGL fixed-function) and was merged
in. It is **ported**, not vendored: it runs on the same core 3.3 context as the city, so
there is no fixed-function code anywhere and no PyOpenGL dependency. What survived
unchanged is the part that matters — the palette, the greedy mesher and the per-cell
brightness hash, now in `voxity/voxel.py` where both halves can reach them.

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
./env/bin/voxity                                # start screen, then map, then play
./env/bin/voxity --place rathaus --size 1200    # straight into the city
./env/bin/voxity --center 53.5503,9.9937 --size 2000
./env/bin/voxity --no-map                       # skip the picker, default square
./env/bin/voxity --editor                       # straight into the voxel editor
./env/bin/voxity --list-places                  # 14 Hamburg presets in main.py
./env/bin/voxity --build-footprints             # survey footprint shapes, then exit
```

`--build-map` and `--build-footprints` are checked before anything else in `main.main`
and both `return` rather than falling through, so neither opens a window; they are the
two offline tools.

**Naming a tool suppresses everything before it.** `--place`/`--center`/`--bbox`,
`--no-map` and `--screenshot` (which has nobody to click) go straight to the direct city
path; `--editor` goes straight to the editor. `main.run_windowed` calls this `forced`,
and a forced run does exactly one pass and exits rather than falling back to the menu, so
every scripted invocation behaves as it always did. Only the bare-argument case changed.

**`--frames` also terminates the loop after one pass**, for the same reason: every stage
(start screen, picker, fly, editor) gives up after N frames by handing control *back*, so
without that check `--frames 3` would cycle for ever.

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

There are no unit tests. Nine ways to verify a change without a human at the keyboard,
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

# 5. the whole chain: start screen, then picker, 3 frames each, then quits
./env/bin/voxity --frames 3

# 6. the editor's 3D view and voxel shader, headless (EGL, no display)
./env/bin/voxity --editor --screenshot forge.png

# 7. the editor in the real window, quits by itself
./env/bin/voxity --editor --frames 3

# 8. the footprint survey — no GL at all, and cached after the first run
#    (cold: ~4m40s over Hamburg, all of it the osmium read plus rasterising;
#     warm: 2s, of which grouping is 1.4s — so iterate on thresholds freely)
./env/bin/voxity --build-footprints --footprint-count 6 --footprint-sizes 2

# 9. a footprint opened as a model, which is the point of (8)
./env/bin/voxity --editor --model models/footprints/footprint-00-0-12x9.json \
    --screenshot fp.png
```

(1) works because `main.run_windowed` and `main.render_headless` import moderngl and
pygame *inside* the function — extract and build never touch GL, so this still passes when the
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

(6) is the cheap voxel check but it draws no UI, so ui.py is untested by it. To see the
editor's chrome or the start screen without a human, wrap `pygame.display.flip` to read
`ctx.screen` *before* it swaps (afterwards the back buffer is undefined) and call
`startscreen.choose` / `editor.run` with `frames=`.

The invariant worth testing on the voxel side is that the shader's mosaic agrees with
`voxel.value_for_cell` on the CPU. Mesh a flat single-hue wall — it greedy-merges to one
quad, so every brightness square on it comes from the shader alone — then sample the
middle of each cell and compare with `color_rgb(hue, value_for_cell(cell))` times
`face_brightness(normal)`. Do it once at large negative coordinates too: that is where
GLSL's 32-bit multiply wraps and Python's does not, and the bitmask is what makes them
agree anyway.

## Pipeline

There are **four** pipelines. Three run over the `.osm.pbf` and share only tags.py and
geo.py; the fourth has nothing to do with OSM at all and meets the first one at the
vertex buffer:

```
play:   .osm.pbf → extract.py → Scene → build.py ─┐
map:    .osm.pbf → overview.py ────────────────── │ → PNG → mapview.py → GL
voxel:  models/*.json → voxel.py ─────────────────┴→ (verts, trees) → renderer.py → GL
                                 └→ editor/render.py → GL   (the editor's own view)
shapes: .osm.pbf → footprints.py → models/footprints/*.json ─┘  (via the editor,
                                                                 by hand)
```

The shapes pipeline is the only one with a **human in the middle**, and it is offline:
`--build-footprints` reduces every building in the extract to a mask on the voxel grid
and writes the commonest ones out as one-layer voxel models, which you then open in the
editor and build upwards into a house. Nothing at runtime reads `models/footprints/`
yet — placing those models back into a city is not written.

The voxel pipeline exists twice on purpose. `voxel.mesh_vertices` emits the **shared
layout from mesh.py**, so a model can go into a city's buffer and be lit by its sun and
shadow map (`MAT_VOXEL`, see below); `editor/render.py` draws the very same buffer with
its own flat program instead, because in the editor a hue on screen must be the hue in
the palette — no shadows, no fog, no tone mapping.

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
- **voxity/geo.py** — `Projection` (equirectangular around the box centre), the
  Sutherland-Hodgman / Liang-Barsky clippers, and the ring helpers `dedup_ring`,
  `signed_area` and `oriented_box`. The last three were private to build.py until
  footprints.py needed the same min-area rectangle; `oriented_box(ring, tol=0)` is
  bit-for-bit the old behaviour, and only a non-zero `tol` engages the tie-break
  described under "Retuning the footprint survey".
- **voxity/mesh.py** — the vertex layout, `MeshBuilder`, and the `MAT_*` constants.
  Owned by neither producer, because build.py and voxel.py both feed it.
- **voxity/build.py** — `build_scene` draws ground skirt → surfaces (by layer) → lines
  (by elev, layer) → buildings, through `MeshBuilder`.
- **voxity/renderer.py** + **shaders.py** — shadow pass, scene pass, instanced trees,
  fullscreen sky. GLSL lives as strings in shaders.py, sharing a `COMMON_LIGHTING` chunk.
- **voxity/camera.py** — matrix helpers (`perspective`, `ortho`, `look_at`, `to_gl`),
  the fly camera, the editor's `OrbitCamera`, and `screen_ray` (the replacement for
  `gluUnProject`, which core profile does not have). **voxity/hud.py** — pygame-rendered
  text panels uploaded as a GL texture. **voxity/ui.py** — flat 2D widgets (rects,
  outlines, cached text, `Menubar`) in **pixels with y down**, so a widget's hit test is
  its draw rectangle. Solid shapes batch into one buffer and keep insertion order, which
  is how a dropdown lands on top; text draws afterwards over all of it.
- **voxity/overview.py** — bakes the whole extract into one flat PNG. Two passes:
  `data_bbox` histograms one node per object to find where the data actually *is*, then
  `bake` streams triangles through `_Batch`. Needs a live GL context, so it is imported
  lazily; `--build-map` bakes it headlessly through the same EGL path as `--screenshot`.
- **voxity/footprints.py** — the offline shape survey. Streams the extract like
  overview.py does (a mask per building, never a `Scene`), rotates each footprint onto
  its own walls, rasterises it to the voxel grid, and groups the results. **No GL, no
  pygame at import** — pygame is imported inside `write_sheet` only, which is why
  main.py can import it at the top without breaking the GL-free smoke test.
- **voxity/voxel.py** — the model (`{(x,y,z): hue}`), the palette, the per-cell value
  hash, exterior-cavity culling, the greedy mesher and JSON load/save. **No GL, no
  pygame** — that is what lets the city import it. `mesh_vertices(quads, scale, offset)`
  is the bridge to the city: `scale` is the cell size in metres.
- **voxity/editor/** — the editing half. `app.py` owns the loop and the layout, `pick.py`
  is the DDA ray march, `render.py` the two GL programs, `io.py` the file dialog and the
  OBJ/PNG exports.
- **voxity/startscreen.py** — the menu. Owns its own loop like mapview.py and returns
  `'play'` / `'editor'` / `'quit'`.
- **voxity/mapview.py** — the region picker. Owns its own event loop, returns a lon/lat
  box, and knows nothing about how the map was made. Regions are a **fixed grid**, not a
  freely placed rectangle: `MapView` divides `omap.extent` into `--size`-metre cells,
  centred so the remainder is split between both edges, and you select one by index.
  Cell size is fixed for the life of the picker, so `count`/`origin`/`cell` are computed
  once in `__init__` — anything that changes `size_m` later has to recompute all three.
  The grid and the selected cell are drawn by `MAPVIEW_FS` from uniforms, not as
  geometry, so a 31 × 30 grid costs no more than a 1 × 1 one.

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
mesh.py, consumed by `Renderer.scene_vao`, read by `SCENE_VS`. `MAT_MATTE` 0,
`MAT_WATER` 1 (ripple normal + specular), `MAT_VOXEL` 2 (the per-cell mosaic).
`SCENE_FS` switches on `v_mat` **highest first**; a material with no branch falls through
to matte, and a new one has to keep its distance from the others by more than 0.5.
`u_voxel_cell` is one uniform for the whole scene, so every voxel model in a city has to
be meshed at the same `scale` that `Renderer.voxel_cell` is set to, or the mosaic stops
lining up with the geometry. Trees are a separate program and buffer entirely
(`tree_mesh` + per-instance `x, z, height, radius, tint`), so the material slot never
sees foliage. Changing the layout means touching all three places,
plus the `'3f 28x'` stride in `scene_depth_vao`.

**Four cache versions, and they are not symmetric.** `extract.CACHE_VERSION` keys the
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

`footprints.FOOTPRINT_VERSION` is the fourth, and it is on its own branch like the map's:
it keys the pickled mask survey, and its cache key also folds in every constant that
changes what a mask *is* (`CELL`, `SUPERSAMPLE`, the area and cell caps, `FRAME_TOL`,
`NORM_LONG`) — so retuning those invalidates it without a version bump, while changing
the grouping thresholds deliberately does *not*, because re-grouping a cached survey is
the fast half. Bump `FOOTPRINT_VERSION` when the survey's *format* changes.
Footprint output is not a cache: `models/footprints/` is checked-in-able work product
and nothing deletes it, so a re-run overwrites `footprint-*.json` in place. Move a model
you have started building **out** of that directory before re-running.

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

**The voxel look is a hash, evaluated twice.** A voxel's colour is `(hue, value)` at
fixed saturation, and the *value* is never stored: it is
`VALUE_POOL[hash(cell) % 8]`. Python computes it in `voxel.value_for_cell`; GLSL
recomputes it per fragment in `shaders.voxel_value_glsl()`, which is **generated** from
the palette constants precisely so the two cannot drift. Three things make that work and
each is easy to break:

* `hsv_to_rgb` is linear in V at fixed H and S, so the CPU hands over the *full-value*
  colour and the shader multiplies the per-cell factor in. Change the palette to
  something non-linear in V and the shader stops matching per-voxel shading.
* The shader recovers the cell from the fragment's **world position**, stepping half a
  cell back along the normal first so a fragment exactly on a face floors into the cell
  that owns it. This is what makes the mosaic survive greedy meshing: one merged quad
  covering a whole wall still draws one brightness square per voxel.
* `& 7` stands in for `% len(VALUE_POOL)`, which is only valid while that length is a
  power of two — `voxel_value_glsl` raises if it isn't. It is also why the two agree at
  large coordinates, where GLSL's 32-bit multiply wraps and Python's does not: wrapping
  mod 2³² preserves the low three bits.

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

Voxel models are **not** cached — they are meshed on load and on every edit — so none of
the four versions covers them and none needs bumping for a voxel change.

## Retuning the footprint survey

`--build-footprints` ranks on **two axes on purpose**, and collapsing them is the trap:
rank on shape alone and the output is one rectangle where Hamburg has thirty sizes of
them; rank on the voxel masks directly and all 24 slots are rectangles, because that is
honestly what a city is mostly made of. So a *family* is the shape with its size
normalised away, and each family emits its commonest few real sizes
(`--footprint-sizes`). Both halves have to stay.

`--footprint-count` counts **families**, not models, and that is load-bearing: Hamburg's
first ten families are all rectangles at different proportions, so a cap on models never
reaches the L-shapes — which is the exact failure the two axes exist to prevent. Output
is up to `count * sizes` files. A run over Hamburg at the defaults gives rectangles for
families 0–9 (`fill 1.00`, 70% of all footprints), then L-shapes with varying notches
from 10 on, and a chamfered octagon around 22.

`MIN_SHAPE_COUNT` is the other cost knob. Grouping is quadratic in candidates and 66k of
Hamburg's 74k distinct shapes occur exactly *once*, so shapes below the threshold are
skipped: they can neither form a top family nor reorder one. It costs coverage, not
correctness — `families()` prints the percentage of footprints that survived, and
`write` divides `share` by the full surveyed total rather than by the survivors so the
index does not quietly inflate. Two prunes keep `align` off the critical path and both
are *sound* (they can never discard a real match): a bounding box more than
`ALIGN_MARGIN` different cannot match, and since overlap never exceeds the smaller area
over the larger, neither can a shape whose filled-cell count is outside
`[iou * n, n / iou]`. Dims alone are nearly useless as a filter here — almost every
normalised shape is 20-by-something, so the filled-count band is what does the work.

Three constants interact, and a change to one needs the other two re-checked — the way
to do that is a sweep over a handful of synthetic shapes (rectangle, L, T, U, octagon)
asking two questions at once: does one shape at several *scales* stay one family, and do
the five shapes stay five families? Optimising either alone gives a wrong answer.

* **`NORM_LONG`** has to resolve the thinnest feature worth keeping. At 12 a T-stem a
  fifth of the width normalises to 2.4 cells, thresholds to 2 or 3 depending on where
  the grid falls, and one real T lands in three families. 20 fixes it.
* **`IOU_JOIN`** must stay *above* the overlap of two shapes you want kept apart. An
  octagon and its bounding square overlap about 0.875, so dropping the threshold to 0.85
  to be more forgiving silently merges every rounded building into "square".
* **`FRAME_TOL`** exists because a shape with near-45-degree symmetry has several edge
  directions bounding nearly the same area, and `oriented_box` picking the numerically
  smallest makes the frame flip as the building rotates — an octagon then spreads over a
  dozen families. Non-zero `tol` breaks the tie on how much perimeter runs along the
  frame instead. It is **off by default** so build.py's gable fitter is untouched; if you
  ever turn it on there, that is a `MESH_VERSION` bump.

Two further things that look like bugs and are not. `normalise` must be given the
footprint's **metric** span, not the mask's cell dimensions: cell dims are already
rounded, so a 16 x 20 m L is 13 x 16 cells at one size and 22 x 28 at another, and those
do not round to the same twentieths. And scale invariance holds at the *family* level
only — a 9.6 m square on a 1 m grid genuinely loses a corner cell that a 12 m one keeps,
so asserting equal canonical keys across scales will fail; assert that `families()` puts
them together.

## Style

Ruff is configured in `pyproject.toml` (line-length 95, single quotes) and installed in
`env`: `./env/bin/ruff check .`. It currently reports 3 pre-existing findings —
two `BLE001` blind `except Exception` around osmium ring materialisation (deliberate; two
sibling sites already carry `# noqa: BLE001`) and one `B905` `zip()` without `strict=`.

Existing code is plain functions and dicts, single quotes, numpy-vectorised where it
matters, with comments that explain *why* an odd thing is there (coarse rejection, ring
re-winding, part insetting). Match that: prefer array ops over per-feature Python loops in
extract/build — those run over hundreds of thousands of features.
