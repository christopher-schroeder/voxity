#!/usr/bin/env python
"""Render a square of an .osm.pbf as a 3D city, or edit the voxel models in it.

    python main.py                              # start screen: city or editor
    python main.py --center 53.5503,9.9937 --size 1500
    python main.py --place speicherstadt --size 900
    python main.py --bbox 9.98,53.54,10.00,53.56
    python main.py --editor                     # straight into the editor
"""

import argparse
import math
import os
import sys
import time

import numpy as np

from voxity import extract, footprints, voxel
from voxity.build import build_scene
from voxity.camera import Camera
from voxity.geo import square_bbox

DEFAULT_PBF = 'hamburg-260728.osm.pbf'
# Geofabrik names its dated snapshots exactly like DEFAULT_PBF, so the default
# extract round-trips to a real URL and can be fetched on demand. Any other
# --pbf needs its own --pbf-url; we can't guess the region path from a filename.
PBF_URL = 'https://download.geofabrik.de/europe/germany/' + DEFAULT_PBF

PLACES = {
    'rathaus':         (53.5503, 9.9937),
    'speicherstadt':   (53.5436, 9.9885),
    'hafencity':       (53.5405, 10.0000),
    'elbphilharmonie': (53.5413, 9.9841),
    'st-pauli':        (53.5500, 9.9640),
    'reeperbahn':      (53.5497, 9.9598),
    'altona':          (53.5503, 9.9350),
    'alster':          (53.5570, 9.9990),
    'hauptbahnhof':    (53.5528, 10.0067),
    'landungsbruecken': (53.5460, 9.9690),
    'eppendorf':       (53.5940, 9.9840),
    'wandsbek':        (53.5820, 10.0850),
    'harburg':         (53.4600, 9.9830),
    'airport':         (53.6304, 9.9882),
}

HELP_LINES = [
    ('WASD / arrows  move', True),
    ('Q E / ctrl space  down up', True),
    ('shift boost   alt crawl', True),
    ('drag mouse or TAB  look', True),
    ('wheel  speed', True),
    (', .  time of day', True),
    ('[ ]  sun azimuth', True),
    ('T trees  L shadows  G fog', True),
    ('R reset  P screenshot  F1 help', True),
    ('M  back to the map', True),
    ('ESC  release / back to the menu', True),
]


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--pbf', default=DEFAULT_PBF, help='input .osm.pbf')
    p.add_argument('--pbf-url', help='where to fetch --pbf from when it is missing')
    p.add_argument('--no-download', action='store_true',
                   help='fail on a missing extract instead of downloading it')
    g = p.add_mutually_exclusive_group()
    g.add_argument('--center', help='LAT,LON centre of the square')
    g.add_argument('--place', help='named preset (see --list-places)')
    g.add_argument('--bbox', help='W,S,E,N in degrees (overrides --size)')
    p.add_argument('--size', type=float, default=1200.0,
                   help='edge length of the square in metres, and of the '
                        'map picker\'s grid cells (default 1200)')
    p.add_argument('--width', type=int, default=1600)
    p.add_argument('--height', type=int, default=950)
    p.add_argument('--sun', default='235,34', help='AZIMUTH,ELEVATION in degrees')
    p.add_argument('--no-trees', action='store_true')
    p.add_argument('--no-shadows', action='store_true')
    p.add_argument('--no-cache', action='store_true')
    p.add_argument('--screenshot', help='render one frame headlessly to this PNG')
    p.add_argument('--view', help='initial camera as YAW,PITCH,ALTITUDE')
    p.add_argument('--frames', type=int, default=0,
                   help='quit after N frames (smoke test)')
    p.add_argument('--list-places', action='store_true')
    p.add_argument('--build-map', action='store_true',
                   help='bake the overview map offscreen and exit')
    p.add_argument('--no-map', action='store_true',
                   help='skip the region picker and go straight to the default')
    p.add_argument('--map-size', type=int, default=None,
                   help='long edge of the baked overview map in pixels')
    p.add_argument('--editor', action='store_true',
                   help='skip the start screen and open the voxel editor')
    p.add_argument('--build-footprints', action='store_true',
                   help='cluster the extract\'s building footprints into voxel '
                        'models and exit')
    p.add_argument('--footprint-cell', type=float, default=footprints.CELL,
                   help='voxel cell size in metres for the footprints')
    p.add_argument('--footprint-count', type=int, default=16,
                   help='how many shape families to write')
    p.add_argument('--footprint-sizes', type=int, default=2,
                   help='how many real sizes of each shape to write')
    p.add_argument('--footprint-iou', type=float, default=footprints.IOU_JOIN,
                   help='overlap at which two footprints count as one shape')
    p.add_argument('--footprint-dir', default=footprints.OUT_DIR,
                   help='where the footprint models are written')
    p.add_argument('--model', default=voxel.DEFAULT_MODEL,
                   help=f'voxel model the editor opens (default {voxel.DEFAULT_MODEL})')
    return p.parse_args(argv)


def region_given(args):
    """True when the command line already says where to play."""
    return bool(args.bbox or args.center or args.place)


def resolve_bbox(args):
    if args.bbox:
        w, s, e, n = (float(v) for v in args.bbox.split(','))
        return (min(w, e), min(s, n), max(w, e), max(s, n))
    if args.center:
        lat, lon = (float(v) for v in args.center.split(','))
    elif args.place:
        key = args.place.lower()
        if key not in PLACES:
            sys.exit(f'unknown place {args.place!r}; try --list-places')
        lat, lon = PLACES[key]
    else:
        lat, lon = PLACES['rathaus']
    return square_bbox(lon, lat, args.size)


MESH_VERSION = 1


def download_pbf(url, path):
    """Stream `url` into `path`, writing through a `.part` file.

    The rename is the last step on purpose: a half-written extract left under
    the real name passes the existence check on the next run and then dies deep
    inside osmium, which reads as a parser bug rather than a broken download.
    """
    # only needed on the rare download path, so keep it out of startup
    import urllib.error
    import urllib.request

    tmp = path + '.part'
    print(f'fetching {url}')
    try:
        with urllib.request.urlopen(url, timeout=30) as resp, open(tmp, 'wb') as fh:
            total = int(resp.headers.get('Content-Length') or 0)
            done = 0
            while chunk := resp.read(1 << 20):
                fh.write(chunk)
                done += len(chunk)
                pct = f'  {100 * done / total:5.1f}%' if total else ''
                print(f'\r  {done / 1048576:7.1f} MB{pct}', end='', flush=True)
        print()
    except (urllib.error.URLError, OSError, KeyboardInterrupt) as exc:
        if os.path.exists(tmp):
            os.remove(tmp)
        sys.exit(f'\ndownload failed: {exc}')
    os.replace(tmp, path)


def ensure_pbf(args):
    """Make sure the extract is on disk, fetching it on first run if not."""
    if os.path.exists(args.pbf):
        return
    if args.no_download:
        sys.exit(f'no such file: {args.pbf}  (--no-download given)')
    url = args.pbf_url
    if url is None:
        if os.path.basename(args.pbf) != DEFAULT_PBF:
            sys.exit(f'no such file: {args.pbf}\n'
                     'give --pbf-url to fetch it, or grab an extract from '
                     'https://download.geofabrik.de/')
        url = PBF_URL
    download_pbf(url, args.pbf)


def prepare(args, bbox=None):
    ensure_pbf(args)
    if bbox is None:
        bbox = resolve_bbox(args)
    print(f'bbox  W {bbox[0]:.5f}  S {bbox[1]:.5f}  E {bbox[2]:.5f}  N {bbox[3]:.5f}')
    scene = extract.load(args.pbf, bbox, use_cache=not args.no_cache)

    mesh_file = None
    if not args.no_cache:
        key = extract.cache_key(args.pbf, bbox)
        mesh_file = os.path.join('cache', f'mesh-{key}-{MESH_VERSION}.npz')
        if os.path.exists(mesh_file):
            with np.load(mesh_file) as data:
                verts, trees = data['verts'], data['trees']
            print(f'  mesh from cache: {len(verts) // 3} triangles')
            return scene, verts, (trees[:0] if args.no_trees else trees)

    t0 = time.time()
    verts, trees = build_scene(scene)
    print(f'  built in {time.time() - t0:.1f}s')
    if mesh_file:
        np.savez(mesh_file, verts=verts, trees=trees)
    if args.no_trees:
        trees = trees[:0]
    return scene, verts, trees


def make_camera(scene, view=None):
    """Camera looking north over the middle of the square.

    `view` is an optional 'YAW,PITCH,ALTITUDE' override.
    """
    minx, minz, maxx, maxz = scene.extent
    span = max(maxx - minx, maxz - minz)
    cx, cz = 0.5 * (minx + maxx), 0.5 * (minz + maxz)
    yaw, pitch, alt = 0.0, -27.0, span * 0.42 + 60.0
    if view:
        yaw, pitch, alt = (float(v) for v in view.split(','))
    back = alt / max(math.tan(math.radians(-pitch)), 0.2) if pitch < 0 else 0.0
    back = min(back, span * 0.8)
    return Camera([cx, alt, cz + back], yaw=yaw, pitch=pitch)


def standalone_context():
    """An offscreen GL context, EGL first so it works without a display."""
    import moderngl

    for backend in ('egl', None):
        try:
            return (moderngl.create_standalone_context(backend=backend)
                    if backend else moderngl.create_standalone_context())
        except Exception as exc:                       # noqa: BLE001
            print(f'  standalone context ({backend}) failed: {exc}')
    sys.exit('could not create an offscreen GL context')


def build_map(args):
    """Bake the overview map offscreen, then exit. Works headless."""
    from voxity import overview

    ensure_pbf(args)
    ctx = standalone_context()
    kw = {'use_cache': not args.no_cache}
    if args.map_size:
        kw['long_edge'] = args.map_size
    omap = overview.load_or_bake(ctx, args.pbf, **kw)
    print(f'overview map {omap.size[0]}x{omap.size[1]}  '
          f'{omap.metres_per_pixel:.1f} m/pixel')


def build_footprints(args):
    """Cluster the extract's building footprints into voxel models, then exit.

    No GL at all — this is the pipeline that ends at the editor rather than at a
    vertex buffer, so it runs anywhere the extract does.
    """
    ensure_pbf(args)
    cell = args.footprint_cell
    counts, areas, seen = footprints.collect(args.pbf, cell,
                                             use_cache=not args.no_cache)
    if not counts:
        sys.exit('no usable building footprints in the extract')
    fams = footprints.families(counts, areas, iou_join=args.footprint_iou)
    footprints.write(fams, args.footprint_dir, cell,
                     {'pbf': os.path.basename(args.pbf), 'buildings': seen,
                      'footprints': sum(counts.values()),
                      'distinct_shapes': len({k[0] for k in counts}),
                      'iou_join': args.footprint_iou},
                     count=args.footprint_count,
                     per_family=args.footprint_sizes,
                     total=sum(counts.values()))


def render_headless(args, scene, verts, trees):
    import pygame
    from voxity.renderer import Renderer

    ctx = standalone_context()
    w, h = args.width, args.height
    colour = ctx.texture((w, h), 4, samples=0)
    depth = ctx.depth_renderbuffer((w, h))
    fbo = ctx.framebuffer(color_attachments=[colour], depth_attachment=depth)

    az, el = (float(v) for v in args.sun.split(','))
    r = Renderer(ctx, verts, trees, scene.extent, az, el)
    r.shadows = not args.no_shadows
    cam = make_camera(scene, args.view)
    r.render(fbo, cam, w / h)
    ctx.finish()

    data = fbo.read(components=3)
    surf = pygame.image.frombuffer(data, (w, h), 'RGB')
    surf = pygame.transform.flip(surf, False, True)
    pygame.image.save(surf, args.screenshot)
    print(f'wrote {args.screenshot}')


def render_editor_headless(args):
    """One frame of the editor's 3D view to a PNG, no display needed.

    The cheapest check that the voxel shader still compiles and that a model
    still meshes — the whole path except the event loop.
    """
    import pygame
    from voxity import editor

    ctx = standalone_context()
    w, h = args.width, args.height
    colour = ctx.texture((w, h), 4, samples=0)
    depth = ctx.depth_renderbuffer((w, h))
    fbo = ctx.framebuffer(color_attachments=[colour], depth_attachment=depth)

    ed = editor.Editor(ctx, args.model)
    ed.frame()
    print(f'  {len(ed.voxels)} voxels, {len(ed.remesh()) // 3} triangles')

    fbo.use()
    fbo.clear(0.10, 0.11, 0.13, 1.0, depth=1.0)
    ed.draw_3d((w, h), over_ui=True)
    ctx.finish()

    surf = pygame.image.frombuffer(fbo.read(components=3), (w, h), 'RGB')
    pygame.image.save(pygame.transform.flip(surf, False, True), args.screenshot)
    print(f'wrote {args.screenshot}')


def open_window(args):
    """Create the window and GL context. Returns (ctx, size)."""
    import moderngl
    import pygame

    pygame.init()
    pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MAJOR_VERSION, 3)
    pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MINOR_VERSION, 3)
    pygame.display.gl_set_attribute(pygame.GL_CONTEXT_PROFILE_MASK,
                                    pygame.GL_CONTEXT_PROFILE_CORE)
    pygame.display.gl_set_attribute(pygame.GL_DEPTH_SIZE, 24)
    pygame.display.gl_set_attribute(pygame.GL_MULTISAMPLEBUFFERS, 1)
    pygame.display.gl_set_attribute(pygame.GL_MULTISAMPLESAMPLES, 4)

    size = (args.width, args.height)
    pygame.display.set_mode(size, pygame.OPENGL | pygame.DOUBLEBUF | pygame.RESIZABLE)
    pygame.display.set_caption('voxity — ' + os.path.basename(args.pbf))
    ctx = moderngl.create_context()
    ctx.enable(moderngl.DEPTH_TEST | moderngl.CULL_FACE)
    return ctx, size


def splash(ctx, overlay, size, lines):
    """Put a message on screen before a long blocking step."""
    import pygame

    ctx.screen.use()
    ctx.viewport = (0, 0, *size)
    ctx.screen.clear(0.06, 0.07, 0.08, 1.0, depth=1.0)
    overlay.draw(lines, size, 'tl')
    pygame.display.flip()


def fly(ctx, args, scene, verts, trees, overlay, help_overlay, size):
    """The 3D loop. Returns 'quit', 'menu' (ESC) or 'map' (asked to go back)."""
    import moderngl
    import pygame
    from voxity.renderer import Renderer

    ctx.enable(moderngl.DEPTH_TEST | moderngl.CULL_FACE)
    az, el = (float(v) for v in args.sun.split(','))
    renderer = Renderer(ctx, verts, trees, scene.extent, az, el)
    renderer.shadows = not args.no_shadows
    cam = make_camera(scene, args.view)
    home = (cam.pos.copy(), cam.yaw, cam.pitch)

    default_fog = renderer.fog_density

    clock = pygame.time.Clock()
    looking = False
    show_help = True
    fps = 0.0
    running = True
    outcome = 'quit'

    frame = 0
    while running:
        dt = min(clock.tick(120) / 1000.0, 0.1)
        frame += 1
        if args.frames and frame > args.frames:
            running = False
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.VIDEORESIZE:
                size = (max(320, event.w), max(240, event.h))
                ctx.viewport = (0, 0, *size)
            elif event.type == pygame.MOUSEBUTTONDOWN:
                if event.button in (1, 3):
                    looking = True
                    pygame.event.set_grab(True)
                    pygame.mouse.set_visible(False)
                    pygame.mouse.get_rel()
                elif event.button == 4:
                    cam.speed = min(cam.speed * 1.25, 4000.0)
                elif event.button == 5:
                    cam.speed = max(cam.speed / 1.25, 3.0)
            elif event.type == pygame.MOUSEBUTTONUP and event.button in (1, 3):
                if not pygame.key.get_pressed()[pygame.K_TAB]:
                    looking = False
                    pygame.event.set_grab(False)
                    pygame.mouse.set_visible(True)
            elif event.type == pygame.MOUSEMOTION and looking:
                dx, dy = event.rel
                cam.look(dx, dy)
            elif event.type == pygame.KEYDOWN:
                k = event.key
                if k == pygame.K_ESCAPE:
                    if looking:
                        looking = False
                        pygame.event.set_grab(False)
                        pygame.mouse.set_visible(True)
                    else:
                        outcome = 'menu'
                        running = False
                elif k == pygame.K_TAB:
                    looking = not looking
                    pygame.event.set_grab(looking)
                    pygame.mouse.set_visible(not looking)
                    pygame.mouse.get_rel()
                elif k == pygame.K_t:
                    renderer.show_trees = not renderer.show_trees
                elif k == pygame.K_l:
                    renderer.shadows = not renderer.shadows
                elif k == pygame.K_g:
                    renderer.fog_density = (0.0 if renderer.fog_density > 0
                                            else default_fog)
                elif k == pygame.K_F1:
                    show_help = not show_help
                elif k == pygame.K_m and not args.no_map:
                    outcome = 'map'
                    running = False
                elif k == pygame.K_r:
                    cam.pos, cam.yaw, cam.pitch = home[0].copy(), home[1], home[2]
                elif k == pygame.K_p:
                    os.makedirs('screenshots', exist_ok=True)
                    name = time.strftime('screenshots/voxity-%Y%m%d-%H%M%S.png')
                    data = ctx.screen.read(components=3)
                    surf = pygame.image.frombuffer(data, size, 'RGB')
                    pygame.image.save(pygame.transform.flip(surf, False, True), name)
                    print('wrote', name)

        keys = pygame.key.get_pressed()
        if keys[pygame.K_COMMA]:
            renderer.sun_elevation = max(-8.0, renderer.sun_elevation - 25.0 * dt)
        if keys[pygame.K_PERIOD]:
            renderer.sun_elevation = min(89.0, renderer.sun_elevation + 25.0 * dt)
        if keys[pygame.K_LEFTBRACKET]:
            renderer.sun_azimuth -= 40.0 * dt
        if keys[pygame.K_RIGHTBRACKET]:
            renderer.sun_azimuth += 40.0 * dt
        cam.update(dt, keys)

        ctx.screen.use()
        ctx.viewport = (0, 0, *size)
        renderer.render(ctx.screen, cam, size[0] / size[1], dt)

        fps = fps * 0.92 + (1.0 / max(dt, 1e-4)) * 0.08
        lon, lat = scene.projection.inverse(cam.pos[0], cam.pos[2])
        info = [
            (f'{fps:5.0f} fps   {len(verts) // 3:,} tris', False),
            (f'{lat:.5f}, {lon:.5f}   alt {cam.pos[1]:6.0f} m', False),
            (f'sun {renderer.sun_azimuth % 360:3.0f}deg / {renderer.sun_elevation:2.0f}deg'
             f'   speed {cam.speed:.0f}', True),
        ]
        overlay.draw(info, size, 'tl')
        if show_help:
            help_overlay.draw(HELP_LINES, size, 'bl')
        pygame.display.flip()

    renderer.release()
    return outcome


def run_city(ctx, args, overlay, help_overlay, state):
    """Pick a region if needed, then fly it. Returns 'quit' or 'menu'.

    `state` carries the baked map between visits, so coming back from the
    editor doesn't re-read a 50 MB extract.
    """
    import pygame

    ensure_pbf(args)
    # An explicit region skips the map entirely, so scripted runs behave
    # exactly as they did before the map existed.
    bbox = resolve_bbox(args) if region_given(args) or args.no_map else None

    while True:
        size = pygame.display.get_window_size()
        if bbox is None:
            if state.get('omap') is None:
                state['omap'] = ensure_map(ctx, args, overlay, size)
            picked = mapview_choose(ctx, state['omap'], size, overlay,
                                    help_overlay, args)
            if isinstance(picked, str):          # 'menu' or 'quit'
                return picked
            bbox, args.size = picked

        scene, verts, trees = prepare(args, bbox)
        size = pygame.display.get_window_size()
        outcome = fly(ctx, args, scene, verts, trees, overlay, help_overlay, size)
        if outcome != 'map':
            return outcome
        bbox = None


def run_windowed(args):
    """Open the window, then run whichever half of voxity the player asked for."""
    import pygame
    from voxity import editor, startscreen
    from voxity.hud import Overlay

    ctx, size = open_window(args)
    overlay = Overlay(ctx)
    help_overlay = Overlay(ctx)
    state = {'omap': None}

    # Naming a tool on the command line skips the menu, so every scripted
    # invocation goes straight where it used to.
    forced = ('editor' if args.editor
              else 'play' if region_given(args) or args.no_map else None)
    choice = forced

    while True:
        size = pygame.display.get_window_size()
        if choice is None:
            choice = startscreen.choose(ctx, size, frames=args.frames)
        if choice == 'quit':
            break
        if choice == 'editor':
            outcome = editor.run(ctx, size, args.model, args.frames,
                                 hud=help_overlay)
        else:
            outcome = run_city(ctx, args, overlay, help_overlay, state)
        # `--frames` is a smoke test: one pass through the chain, then out.
        # Without this it would loop forever, since every stage gives up after
        # N frames by handing control back rather than quitting.
        if outcome == 'quit' or forced or args.frames:
            break
        choice = None

    pygame.quit()


def ensure_map(ctx, args, overlay, size):
    """The overview map, baking it (with a message up) if it isn't cached."""
    from voxity import overview

    long_edge = args.map_size or overview.MAP_LONG_EDGE
    if not args.no_cache:
        omap = overview.load(args.pbf, long_edge=long_edge)
        if omap is not None:
            return omap
    splash(ctx, overlay, size, [('baking the overview map...', False),
                                ('about a minute, once per extract', True)])
    omap = overview.bake(ctx, args.pbf, long_edge=long_edge)
    if not args.no_cache:
        overview.save(omap, args.pbf, long_edge=long_edge)
    return omap


def mapview_choose(ctx, omap, size, overlay, help_overlay, args):
    from voxity.mapview import choose_region

    return choose_region(ctx, omap, size, overlay, help_overlay,
                         size_m=args.size, frames=args.frames)


def main():
    args = parse_args()
    if args.list_places:
        for name, (lat, lon) in sorted(PLACES.items()):
            print(f'  {name:18s} {lat:.4f}, {lon:.4f}')
        return
    if args.build_map:
        build_map(args)
        return
    if args.build_footprints:
        build_footprints(args)
        return
    if args.screenshot:
        if args.editor:
            render_editor_headless(args)
            return
        # headless: there is nobody to pick a region, so fall back to the
        # default square the way it worked before the map existed
        scene, verts, trees = prepare(args)
        render_headless(args, scene, verts, trees)
        return
    run_windowed(args)


if __name__ == '__main__':
    main()
