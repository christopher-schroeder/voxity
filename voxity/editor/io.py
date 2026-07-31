"""The exports that only the editor needs.

Model JSON lives in voxel.py, because the city has to read it too; OBJ and PNG
are editor-only, so they live here. Choosing the path is `browse.py` — this used
to hand that to tkinter, whose Unix file dialog is drawn by Tk rather than by
the desktop and whose nested Tcl event loop fought SDL for the mouse.
"""

import os

import pygame

from .. import voxel


def export_obj(voxels, path):
    """Write the greedy-meshed, exterior-culled surface as OBJ plus a per-hue MTL.

    Faces merge by hue, so the OBJ carries one colour per hue at the value
    pool's centre brightness. The per-cell brightness is a live shader effect
    and is not baked into a static mesh.
    """
    mesh = voxel.build_mesh(voxels)
    mtl_path = os.path.splitext(path)[0] + '.mtl'
    materials = {}                             # hue -> material name
    out = ['mtllib ' + os.path.basename(mtl_path)]
    vi = ni = 1
    for normal, hue, corners in mesh:
        if hue not in materials:
            materials[hue] = f'mat{len(materials)}'
        out.append('usemtl ' + materials[hue])
        out.append('vn {} {} {}'.format(*normal))
        for cx, cy, cz in corners:
            out.append(f'v {cx:g} {cy:g} {cz:g}')
        out.append(f'f {vi}//{ni} {vi + 1}//{ni} '
                   f'{vi + 2}//{ni} {vi + 3}//{ni}')
        vi += 4
        ni += 1
    with open(path, 'w') as fh:
        fh.write('\n'.join(out) + '\n')
    with open(mtl_path, 'w') as fh:
        for hue, name in materials.items():
            rgb = voxel.color_rgb(hue, voxel.CENTRE_VALUE)
            fh.write(f'newmtl {name}\n'
                     f'Kd {rgb[0]:.4f} {rgb[1]:.4f} {rgb[2]:.4f}\n\n')
    print(f'exported {len(mesh)} faces ({len(voxels)} voxels) to {path} '
          f'(+ {os.path.basename(mtl_path)})')


def export_png(ctx, size, path):
    """Save the current colour buffer, before any UI is drawn over it."""
    data = ctx.screen.read(components=3)
    surf = pygame.image.frombuffer(data, size, 'RGB')
    pygame.image.save(pygame.transform.flip(surf, False, True), path)
    print(f'wrote {path}')
