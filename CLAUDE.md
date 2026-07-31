# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-binary-ish Python app with **two tools sharing one window and one GL context**:
a city that reads a square out of an `.osm.pbf` extract, turns OSM features into
triangles and flies a camera through them; and a **voxel editor** for the models the
city is made of. The two meet: a building whose footprint matches one of the surveyed
floor plans is drawn as a voxel model rather than extruded (see place.py), so editing a
house changes what the city looks like. Launched bare it shows a start screen and you pick one. The city path
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
./env/bin/voxity --build-houses                 # a default house per footprint, then exit
```

`--build-map`, `--build-footprints` and `--build-houses` are checked before anything else
in `main.main` and all three `return` rather than falling through, so none opens a window;
they are the offline tools. They run in that order as a chain — the survey reads the
extract, the houses read the survey, the city reads the houses.

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

# 10. the default houses — no GL, ~1s, rewrites models/houses/ in place
./env/bin/voxity --build-houses

# 11. the matcher, in the pipeline check of (1): the "placed N of M" line is it
#     (rathaus is the worst case at ~6%; eppendorf or wandsbek show more)
./env/bin/voxity --place wandsbek --size 800 --screenshot city.png

# 12. the same square with every building extruded, for an A/B
./env/bin/voxity --place wandsbek --size 800 --no-voxel-houses --screenshot flat.png

# 13. the editor's city-lighting preview needs a pygame display surface for
#     ui text but no X server; see the SDL_VIDEODRIVER=dummy note below
./env/bin/python -c "import os; os.environ['SDL_VIDEODRIVER']='dummy'; \
import pygame; pygame.init(); pygame.display.set_mode((16,16)); \
import main as M; from voxity.editor import app; \
ctx=M.standalone_context(); ed=app.Editor(ctx); ed.city_light=True; \
f=ctx.framebuffer(color_attachments=[ctx.texture((320,240),4)], \
 depth_attachment=ctx.depth_renderbuffer((320,240))); f.use(); \
ed.draw_3d((320,240), over_ui=True); print('preview ok')"

# 14. the post chain's switches, which is also the cheapest way to see what
#     ambient occlusion and supersampling are each contributing
./env/bin/voxity --place wandsbek --size 400 --no-ao --screenshot noao.png
./env/bin/voxity --place wandsbek --size 400 --supersample 1 --screenshot raw.png
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
`startscreen.choose` / `editor.run` with `frames=`. **With no X server at all** there is a
second way that needs no window: draw into a standalone-EGL framebuffer instead of
`ctx.screen` and call `Editor.draw_3d` / `Editor.draw_ui` directly. It needs
`SDL_VIDEODRIVER=dummy` plus a 16x16 `set_mode` first — not for GL, which comes from EGL,
but because `ui._texture` calls `convert_alpha()` and that wants *a* pygame display
surface.

**The renderer's own resize path has no windowed test.** `Renderer._ensure_buffers`
rebuilds the offscreen buffers whenever the window changes size, and `--frames` never
resizes anything. Drive it directly instead: one `Renderer`, `render(fbo, cam, aspect,
size=(w, h))` over a sequence of sizes including a degenerate one like 100x3, asserting
`_buf_size` follows `supersample` and that the frame is not black. That also covers
`release()` on a renderer that never rendered, where the buffers are still None.

The invariant to test on the placement side is that a house lands **inside the building's
own outline**, at every rotation. Build a ring from a plan's cells, turn it through a
dozen angles, place it, and check every returned vertex is inside that polygon (allow one
cell of slack — the mask is a rasterisation, so a cell may overhang by half of one). That
is the check that catches a mirrored placement, which is the failure mode that looks
plausible: an L placed mirrored is still an L in the right spot, just with its wing on
the wrong side. Do it with an L or a U, never a rectangle — a rectangle is symmetric
under the very transform you are trying to catch.

The invariant worth testing on the voxel side is that the shader's mosaic agrees with
`voxel.value_for_cell` on the CPU. Mesh a flat single-hue wall — it greedy-merges to one
quad, so every brightness square on it comes from the shader alone — render it face-on
through an **orthographic** projection so one cell is a known block of pixels, then
compare the middle of each cell with `color_rgb(hue, value_for_cell(cell))` times
`face_brightness(normal)`. Do it once at large negative coordinates too, for the 32-bit
wrap.

Two things will make that test lie. Enable **depth test and back-face culling**: without
them the wall's far face wins the pixel, its face brightness is half the near one's, and
every cell reads as the darkest level — which looks exactly like a hash mismatch. And
remember `value_for_cell` returns a *value index* to feed `color_rgb`, not a multiplier;
the shader's table is that index already divided by `N_VALS - 1`.

## Pipeline

There are **four** pipelines. Three run over the `.osm.pbf` and share only tags.py and
geo.py; the fourth has nothing to do with OSM at all and meets the first one at the
vertex buffer:

```
play:   .osm.pbf → extract.py → Scene → build.py ─┐
map:    .osm.pbf → overview.py ────────────────── │ → PNG → mapview.py → GL
voxel:  models/*.json → voxel.py ─────────────────┴→ (verts, trees) → renderer.py → GL
                                 └→ editor/render.py → GL   (the editor's own view)
shapes: .osm.pbf → footprints.py → models/footprints/*.json
                → houses.py     → models/houses/*.json  ──→ place.py ──┘
                    (or the editor, by hand)
```

The shapes pipeline is the only one with a **human in the middle**, and its first two
stages are offline. `--build-footprints` reduces every building in the extract to a mask
on the voxel grid and writes the commonest ones as one-layer voxel models;
`--build-houses` stands a few default houses on each of those; you replace those by hand
in the editor. `place.py` closes the loop at play time: it re-derives each real
building's mask, matches it against the plans, and drops one of that plan's houses on it
instead of extruding it. A building that matches nothing is extruded exactly as before,
so the city degrades to what it was rather than to holes in the ground.

**Coverage is bounded by how many footprints exist, not by the matcher.** A plan matches
a building only at its own size (± `MATCH_MARGIN_M`), so the number of *concrete sizes*
the survey wrote is the ceiling. At the defaults (16 families × 3 sizes = 48 plans) about
11% of buildings get a house; measured at 1 m per cell, going to 8 sizes took it to ~23%.
Raising `--footprint-count` instead barely helps — see "Retuning the footprint survey"
for why the two axes are not interchangeable. Lowering `place.MATCH_IOU` is the wrong
knob: it buys coverage by putting houses on buildings whose walls are somewhere else.

Matching costs about **7 ms a building** at a quarter-metre cell (roughly 5 s for a
750-building square), against 1.5 ms at a metre: the masks are sixteen times the cells and
the margin is 3 cells rather than 1, so `align` tries 49 offsets instead of 9. It is
behind the mesh cache, so a square pays once.

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
  fullscreen sky, then **ambient occlusion and a composite**. GLSL lives as strings in
  shaders.py, sharing a `COMMON_LIGHTING` chunk. See "The look" below: the scene pass
  writes linear light into an offscreen buffer and nothing before POST_FS makes a colour
  you could look at.
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
- **voxity/houses.py** — the default house generator. Walls are the footprint's
  perimeter and roofs are repeated **erosion** of the plan, which is what makes them work
  on an L or an octagon and not just a box; models are hollow, since the interior is a
  sealed cavity `voxel.exterior_air` drops anyway. Every dimension is a metre constant
  converted through `_cells`. No GL, no pygame, and no OSM — it only reads and writes
  models.
- **voxity/place.py** — the runtime match. Reuses `footprints.footprint_mask` for the
  building and `footprints.align` for the fit, so there is exactly one rasteriser. Meshes
  each house once per dihedral transform and reuses it across every building of that
  shape, which is what makes it cheap (~0.4 s for a 270-building square).
- **voxity/voxel.py** — the model (`{(x,y,z): hue}`), the palette, the per-cell value
  hash, exterior-cavity culling, the greedy mesher and JSON load/save. **No GL, no
  pygame** — that is what lets the city import it. `mesh_vertices(quads, scale, offset)`
  is the bridge to the city: `scale` is the cell size in metres.
- **voxity/editor/** — the editing half. `app.py` owns the loop and the layout, `pick.py`
  is the DDA ray march, `render.py` the three GL programs, `io.py` the OBJ/PNG exports,
  `browse.py` the open/save dialog and `choose.py` the modal footprint picker (both own
  their loop, like startscreen.py). The footprint a model is built on is `{(x, z)}` on `Editor`, enforced
  in one place — `Editor.allowed` — and stored in the model's JSON under `footprint`, so
  `voxel.save` takes it and `voxel.load_footprint` reads it back. `voxel.load` is
  unchanged and still returns only voxels, which is why every other caller was untouched.
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

## How big a voxel is

**`voxel.CELL_M` is the one place that decides, and it is 0.25 m.** Everything else
derives from it: the survey rasterises at it, a house is proportioned against it,
`place.py` meshes at it, and `Renderer.voxel_cell` is set from it. They all have to
agree — the shader's mosaic is a hash of the *world* cell, so a model meshed at one size
dropped into a scene set to another stops lining up with its own geometry.

Changing it invalidates the footprint survey (its cache key folds the cell in), every
model on disk, and every cached city mesh (`main.prepare` puts the cell in the mesh key
beside the house directory's digest).

**Anything dimensional is stated in metres and converted at the point of use.** This is
the trap the whole rescale was: while a cell was a metre, "3 cells per storey", "1 cell of
slack", "64 cells maximum span" and "a cell is its own size bin" were all secretly
metres, and turning the grid up four times silently made them a 75 cm storey, 25 cm of
slack, a 16 m cap, and a size bin four times too fine. The constants that had to move,
and what each would have broken:

* `houses.STOREY_M` and the rest of its dimensions — otherwise every house is a quarter
  of its proper height.
* `footprints.MAX_SPAN_M` — as cells it becomes a 16 m cap and throws away most of the
  city.
* `footprints.MATCH_MARGIN_M` (via `match_margin`) — slack for fitting two *real* masks,
  used by `Family.consensus` and by `place.Library`. As a cell count the whole pipeline
  gets four times fussier about what counts as the same building. Note this is **not**
  `ALIGN_MARGIN`, which is in cells of a *normalised* mask and correctly does not scale.
* `footprints.SIZE_BIN_M` (via `size_bin`) — a family's real sizes are counted in 0.5 m
  bins rather than per exact cell dimension. Without it two buildings 30 cm apart in width
  land in different bins, each family's modal size collects a fraction of the members it
  should, and `consensus` ends up voting on a single building.

`footprints.SUPERSAMPLE` goes the other way and drops to 1 at a fine cell: half-coverage
needed estimating when a cell was a metre, but at a quarter of one the cell is already
smaller than any feature of a building outline, so the centre sample says the same thing
for a sixteenth of the work.

**Models are written as boxes, not cells.** A three-storey house is 24,000 voxels at this
size, which is 180 kB of JSON one per row. `voxel.boxes` greedily covers them with
same-hue axis-aligned boxes — about 120 of them — and `voxel.save` writes those. This
needed no change to `load`: the 5-element `[x, y, z, size, hue]` row was already the
format for a sized voxel and `block_cells` already took a triple. Only `y >= 0` is
merged, because `block_cells` refuses negative y and a box spanning it would not survive
the round trip.

**`place.py` keeps houses as `(N, 4)` int arrays, not dicts.** The library holds every
house at once; a dict of 24,000 cell tuples is a couple of megabytes and a hundred and
sixty of them is a third of a gigabyte. The dict `build_mesh` wants is built per
(house, transform) and dropped again.

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

**Vertex layout.** `3f 3f 3f 1f 3f` = position, normal, colour, material, **cell** —
declared in mesh.py, consumed by `Renderer.scene_vao`, read by `SCENE_VS`. `MAT_MATTE` 0,
`MAT_WATER` 1 (ripple normal + specular), `MAT_VOXEL` 2 (the per-cell mosaic).
`SCENE_FS` switches on `v_mat` **highest first**; a material with no branch falls through
to matte, and a new one has to keep its distance from the others by more than 0.5.

**`cell` is the vertex's position in its own model's cells, and that is what makes the
mosaic turn with the model.** It used to be derived from world position, and a house
`place.py` stood on a building that did not run north-south then wore a lattice at an
angle to its own voxels — worst on the roof, where nothing else gave the orientation
away. Three cheaper-looking fixes do not work: the material slot cannot carry a rotation
*and* a phase; deriving a tangent frame from the normal is fine for walls and ambiguous
for exactly the horizontal faces that showed the problem; and baking the value into the
vertex colour destroys greedy merging, since neighbouring cells hash differently and a
house goes from a few hundred triangles to a few thousand. Three floats a vertex is
about 3 MB over a 1200 m square, which is nothing.

`mesh.py` nudges it **half a cell inside the surface** so the shader needs neither the
face normal nor a cell size: the offset is perpendicular to the quad, so it changes
nothing in the plane, and a fragment exactly on a face still floors into the cell that
owns it. That is also why there is no longer a `u_voxel_cell` uniform and no rule about
meshing every model at one scale — models at different cell sizes can now coexist in one
buffer. `MeshBuilder.add` takes `cell=None` for everything that is not `MAT_VOXEL`, and
its re-winding permutes the cell rows along with the positions.

`basis` must be a **rotation**, never a reflection: normals go through it unchanged and a
reflection turns every face inside out under back-face culling. `place.py` mirrors a
model by mirroring its *voxels* and re-meshing, which is free — it happens at most eight
times per house however many buildings ask for it. Note that greedy meshing is **not**
rotation-invariant (it scans row-major), so the same house turned 90 degrees is congruent
but has a different vertex count; compare surface area, not vertices.

Trees are a separate program and buffer entirely
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
changes what a mask *is* (`CELL`, `SUPERSAMPLE`, the area and span caps, `FRAME_TOL`,
`NORM_LONG`) — so retuning those invalidates it without a version bump, while changing
the grouping thresholds deliberately does *not*, because re-grouping a cached survey is
the fast half. Bump `FOOTPRINT_VERSION` when the survey's *format* changes.
Footprint output is not a cache: `models/footprints/` is checked-in-able work product
and nothing deletes it, so a re-run overwrites `footprint-*.json` in place. Move a model
you have started building **out** of that directory before re-running.

**The mesh cache key also folds in the house directory**, via `place.signature` — a
sha1 over the names, sizes and mtimes in `models/houses/`. The houses are *data*, so no
version constant would ever notice you editing one, and a stale `.npz` would silently
keep the old building. `--no-voxel-houses` gets its own key (`-flat`) rather than being
applied afterwards, because unlike `--no-trees` an extrusion cannot be recovered from a
cached mesh that has a house in it.

`--no-trees` is applied *after* the mesh cache is read (`main.prepare` slices `trees` to
length zero), so trees are always meshed and stored: changing what `tree_instances`
produces still needs a `MESH_VERSION` bump, but toggling the flag never does. `tree_mesh`
is a different matter — the *shape* of a tree is built at `Renderer` construction and is
not in any cache, so editing it needs no bump at all. Nothing about the grade or the post
chain is cached either; only vertex colours are, which is why lightening `GROUND_COLOUR`
was a bump and lowering `ambient` was not.

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
* The shader reads the cell straight off the vertex (see the layout above), which is
  what makes the mosaic survive greedy meshing: one merged quad covering a whole wall
  still draws one brightness square per voxel.
* `& 7` stands in for `% len(VALUE_POOL)`, which is only valid while that length is a
  power of two — `voxel_value_glsl` raises if it isn't.
* **The hash needs a real avalanche, and taking low bits off a multiply is not one.**
  This was `(x*p1) ^ (y*p2) ^ (z*p3)` read from its bottom three bits, and the low bits
  of a product depend only on the low bits of the constant: with those primes it
  collapsed to `(5x ^ 7y ^ 7z) & 7`, a lattice of period 8 in every axis that reads on a
  wall as stripes rather than as grain. `HASH_MIX` folds the high bits back down first.
  GLSL does it in `uint`, where the wrap is defined (it is undefined for signed), and
  `uint(int)` is exactly the reinterpretation Python's `& 0xFFFFFFFF` performs, so
  negative cells agree too.

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

Voxel models are **not** cached in the editor — they are meshed on load and on every edit
— so none of the four versions covers them and none needs bumping for a voxel change. A
model placed in a *city* is different: it is baked into the `.npz` like everything else,
and `place.signature(models/houses/)` in the mesh key is what notices you edited it.

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

## The look

The city is graded to a warm late-afternoon voxel-builder look. Four things carry it, and
each one is easy to undo by accident:

**Everything is drawn in linear light, and only POST_FS makes a picture.** The scene pass
writes into an offscreen RGBA16F buffer; `SCENE_FS`, `TREE_FS` and `SKY_FS` all end at
`apply_fog` and hand over unbounded linear values. Tone mapping there instead is not just
untidy — ambient occlusion has to multiply the light *before* the curve compresses it, or
the crevices it darkens come back out grey. It is also what lets the sky be graded like
the ground it meets rather than the two meeting at a seam on the horizon. **A new
material or program that draws into the scene buffer must not tone map.**

**The curve is ACES-filmic, not Reinhard.** The old `x / (x + 0.85)` put a sunlit wall and
one in shadow within a factor of 1.6 of each other on screen when the light on them
differed by seven: the whole city read as an overcast day whatever the sun was doing.

**`sun_palette` is daylight at the top and golden hour at the bottom.** The default sun is
high (215, 52) and the picture is daylight; winding the elevation down with `,` warms it
towards a sunset, which is what the low end is for. The blend runs out at 50 rather than
the ~25 physics would put it, so the warm end is reachable at all. `ambient` scales sky
and bounce together and is the single number separating daylight from overcast.

**Ambient occlusion is contact darkening, not a dome.** `ao_radius` is 1.8 **metres** and
that is small on purpose: at the 4.5 m that looked right on a street corner, one radius
covers an entire tree canopy and the tree goes uniformly black. Three details in AO_FS
each cost a debugging session:

* The normal comes from `dFdx`/`dFdy` of the reconstructed view position, since the vertex
  layout has no room for a normal buffer. It must be flipped to face the *camera*
  (`dot(N, normalize(-P))`), not merely to +z.
* The depth bias must grow with the depth slope. A surface seen edge-on moves metres
  across one pixel and a constant bias lets it occlude itself — whole roofs come out
  black.
* `smoothstep` with `edge0 > edge1` is undefined in GLSL and returns 1 everywhere on this
  driver. That is how the vignette silently did nothing for three iterations.

**Antialiasing is supersampling, not MSAA.** `SUPERSAMPLE = 1.5` renders the scene buffer
larger and the composite's LINEAR fetch is the downsample. MSAA cannot help with what is
here even when the window requests it: the voxel mosaic and the value hash are computed
*inside* triangles, where multisampling takes one sample. It costs about 45% of the frame
— roughly 47 fps against 81 at 1600x950 over a 1200 m square — so `--supersample 1` is the
knob to reach for on a slow machine, not `--no-ao`, which is free to within noise.

Every grading constant lives as an attribute on `Renderer.__init__` rather than inside
POST_FS, so they can be swept without recompiling a shader — `scratchpad/sweep.py`-style
harnesses that instantiate a Renderer and `setattr` overrides are how these were found,
and one-at-a-time iteration is much slower than tiling four at once.

## Why the editor has its own voxel program

`VOXEL_FS` is four lines of lighting; the *substance* is already shared. The vertex
layout (mesh.py), the mesher and palette (voxel.py) and the mosaic hash are common, and
the hash is **generated** into both programs by `shaders.voxel_value_glsl()` from the
same constants, so a change to it lands in both without anyone remembering to. `place.py`
hands the very same buffer to the city.

What is not shared is the grade, and deliberately: **the editor is a colour-picking
tool**. Click hue 9 and the model has to be hue 9. Through the city's chain it is not —
sun colour, a shadow-map lookup, ambient, fog, then AO, ACES, saturation, split tone and
a vignette — and what you see then depends on the sun angle, whether that face is in
shadow, and where on screen it happens to be. Picking colours through a grade is
guesswork. The swatch is `(0.33, 0.50, 0.25)` and the editor draws `(0.29, 0.44, 0.22)`;
the city gives you something else entirely.

**`C` / View → City Lighting** is the answer to the real complaint behind that, which is
that you cannot otherwise tell what a hand-built house will look like in the game. It
draws the model through the actual `Renderer` — the same class the city builds — so it is
not an imitation that can drift. Three things it has to do:

* **Scale to metres.** The city decides shadow length, AO radius and fog in metres, so
  `Editor.city_verts` multiplies positions by `voxel.CELL_M`. It must *not* touch the
  cell attribute: that is what the mosaic hashes and is in cells on purpose.
* **Stand it on ground.** Half of what the look does to a building is the contact shadow
  and the occlusion where it meets the ground, and a model floating in the sky shows
  neither.
* **Put the cursors back afterwards.** `Renderer` owns its passes and clears the target's
  depth in the composite, so nothing survives to depth-test against; the hover and
  placement boxes are drawn on top by `EditorRenderer.draw_cursors`, which takes a packed
  view-projection rather than a camera because the scale has to ride in the matrix — that
  geometry is in cells.

`Renderer.set_geometry` exists only for this: the preview re-uploads on every edit, and
rebuilding the Renderer between keystrokes would recompile five programs and reallocate a
2048-square shadow map. It clears `_shadow_key`, since the sun has not moved but what it
shines on has.

Two things that look like bugs here and are not. `ctx.screen` **is None in a standalone
context**, so `draw_city` targets `ctx.fbo or ctx.screen` in that order — `ctx.fbo` is
the currently bound framebuffer and is what the headless checks need. And a preview shot
where toggling `shadows` changes nothing is almost always a camera with the cast shadow
hidden behind the model; at the default sun the shadow falls towards +x/-z, so look from
yaw 215 before concluding the shadow map is broken.

## Dialogs are drawn in the window, not by another toolkit

`browse.py` (open/save) and `choose.py` (footprints) are modal loops of our own,
drawing with ui.py. Open/save used tkinter's `filedialog` and it was wrong twice over:
Tk draws its own file dialog on Unix — that grey listbox is Tk's, not the desktop's, and
no theme changes it — and it runs a nested Tcl event loop beside SDL's, which is what
left its buttons not responding to clicks. **Do not reach for tkinter again.** Nothing
else can be holding the mouse when the loop reading the mouse is ours.

Shelling out to `zenity`/`kdialog` is the other obvious idea and is worse here: it is a
guess about the desktop, and this machine has none of them installed.

`browse.layout` is shared by the drawing and by `row_at`, which is how the hit test stays
the draw rectangle (ui.py's rule). `paint` is separate from the loop so the dialog can be
rendered into an offscreen buffer and looked at without a window — the same trick the
editor's chrome needs, and the only way to check a dialog whose whole complaint was that
it was ugly.

Clicking means different things in the two modes on purpose: **opening**, a click on a
file is the answer; **saving**, it only copies the name into the field, because the answer
is what the field says at Enter and a stray click should not quietly pick a different file
to overwrite.

## The editor's triangle overlay

`T` / **View → Show Triangles** draws the model's own vertex buffer a second time with
`ctx.wireframe`, through `WIRE_VS`/`WIRE_FS` — position only, one uniform colour. It is a
second VAO over the *same* buffer (`'3f 28x'`, exactly like `scene_depth_vao`), so what
you see is the triangulation that was drawn and not a reconstruction of it. `LINE_VS`
cannot serve here: it takes a colour per vertex, and a wireframe in the model's own hues
is invisible against the model.

The one thing that needs care is depth. The lines come from the same vertices as the
surface under them, so they land at exactly its depth: `'<'` rejects every one of them,
and `'<='` keeps them only where the interpolated depth happens to round the same way,
which stipples every edge that is not axis-aligned on screen. `ctx.polygon_offset =
-1.0, -1.0` for the duration is the fix. Reset both `wireframe` and `polygon_offset`
immediately — the context is shared with the city and with ui.py, both of which draw
triangles.

## Houses, and putting them on buildings

`houses.py` writes the defaults, `place.py` puts them down. Two constants decide *whether*
a house is used at all, and they are separate on purpose:

* **`place.MATCH_IOU` (0.90)** is deliberately higher than the survey's `IOU_JOIN` (0.88).
  Joining two shapes into a family only has to decide they are the same *kind* of shape;
  this decides a specific house may stand where a specific building is, and a wall a metre
  out is a wall a metre out. Do not reuse one for the other.
* **`HEIGHT_TOL_M` / `HEIGHT_TOL_REL`** are what keep a tower block a tower block. The
  plans fit an office block's floor perfectly well, and without a height check every one
  of them gets a five-storey house. `Plan.pick` returns None rather than the least-bad
  house, and None means extrude.

Which house a matched building gets is `place._pick`, a hash of the OSM id — **never
`random`**, for the sharper reason than usual that the mesh is cached: a second run that
picked differently would contradict the `.npz` the first one wrote.

A house voxel that sits outside its own footprint has nowhere to go — `Plan.quads` maps
through the plan's cells and nothing else — so it is dropped, and `place.load` says so on
the console. Generated houses never do this (walls are the perimeter, roofs erode inwards);
a hand-edited one can, if you clear the footprint in the editor and then build outside it.

Two things in `houses.py` that look arbitrary and are not. The **roof rise is capped at
`top // 3`**: left to run, erosion stops only when the plan is used up, which is half the
short span — a real 45-degree pitch, but on a 12 m deep plan it buries the one storey
underneath it. And every detail hue goes through **`contrast()`**, because saturation and
value are fixed by the palette (`voxel.py` owns them) so hue is the only thing telling a
window from the wall around it; blue glass on a blue wall is invisible, and four of the
nine wall hues are blue.

## Style

Ruff is configured in `pyproject.toml` (line-length 95, single quotes) and installed in
`env`: `./env/bin/ruff check .`. It currently reports 3 pre-existing findings —
two `BLE001` blind `except Exception` around osmium ring materialisation (deliberate; two
sibling sites already carry `# noqa: BLE001`) and one `B905` `zip()` without `strict=`.

Existing code is plain functions and dicts, single quotes, numpy-vectorised where it
matters, with comments that explain *why* an odd thing is there (coarse rejection, ring
re-winding, part insetting). Match that: prefer array ops over per-feature Python loops in
extract/build — those run over hundreds of thousands of features.
