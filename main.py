#!/usr/bin/env python
"""Render a square of an .osm.pbf as a 3D city.

    python main.py --center 53.5503,9.9937 --size 1500
    python main.py --place speicherstadt --size 900
    python main.py --bbox 9.98,53.54,10.00,53.56
"""

import argparse
import math
import os
import sys
import time

import numpy as np

from osmcity import extract
from osmcity.build import build_scene
from osmcity.camera import Camera
from osmcity.geo import square_bbox

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
    ('ESC  release / quit', True),
]


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--pbf', default='hamburg-260728.osm.pbf', help='input .osm.pbf')
    g = p.add_mutually_exclusive_group()
    g.add_argument('--center', help='LAT,LON centre of the square')
    g.add_argument('--place', help='named preset (see --list-places)')
    g.add_argument('--bbox', help='W,S,E,N in degrees (overrides --size)')
    p.add_argument('--size', type=float, default=1200.0,
                   help='edge length of the square in metres (default 1200)')
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
    return p.parse_args(argv)


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


def prepare(args):
    if not os.path.exists(args.pbf):
        sys.exit(f'no such file: {args.pbf}')
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


def render_headless(args, scene, verts, trees):
    import moderngl
    import pygame
    from osmcity.renderer import Renderer

    ctx = None
    for backend in ('egl', None):
        try:
            ctx = (moderngl.create_standalone_context(backend=backend)
                   if backend else moderngl.create_standalone_context())
            break
        except Exception as exc:                       # noqa: BLE001
            print(f'  standalone context ({backend}) failed: {exc}')
    if ctx is None:
        sys.exit('could not create an offscreen GL context')

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


def run(args, scene, verts, trees):
    import moderngl
    import pygame
    from osmcity.hud import Overlay
    from osmcity.renderer import Renderer

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
    pygame.display.set_caption('osm city — ' + os.path.basename(args.pbf))
    ctx = moderngl.create_context()
    ctx.enable(moderngl.DEPTH_TEST | moderngl.CULL_FACE)

    az, el = (float(v) for v in args.sun.split(','))
    renderer = Renderer(ctx, verts, trees, scene.extent, az, el)
    renderer.shadows = not args.no_shadows
    overlay = Overlay(ctx)
    help_overlay = Overlay(ctx)
    cam = make_camera(scene, args.view)
    home = (cam.pos.copy(), cam.yaw, cam.pitch)

    default_fog = renderer.fog_density

    clock = pygame.time.Clock()
    looking = False
    show_help = True
    fps = 0.0
    running = True

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
                elif k == pygame.K_r:
                    cam.pos, cam.yaw, cam.pitch = home[0].copy(), home[1], home[2]
                elif k == pygame.K_p:
                    os.makedirs('screenshots', exist_ok=True)
                    name = time.strftime('screenshots/osm-%Y%m%d-%H%M%S.png')
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

    pygame.quit()


def main():
    args = parse_args()
    if args.list_places:
        for name, (lat, lon) in sorted(PLACES.items()):
            print(f'  {name:18s} {lat:.4f}, {lon:.4f}')
        return
    scene, verts, trees = prepare(args)
    if args.screenshot:
        render_headless(args, scene, verts, trees)
    else:
        run(args, scene, verts, trees)


if __name__ == '__main__':
    main()
