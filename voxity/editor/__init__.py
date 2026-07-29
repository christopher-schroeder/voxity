"""The voxel editor — where the objects the city is built from are made.

`voxity.voxel` holds the model, the palette and the mesher, because the city
needs those too; everything under here is the editing half. `run` drives the
editor against a window main.py already opened.
"""

from .app import DEFAULT_MODEL, Editor, run

__all__ = ['DEFAULT_MODEL', 'Editor', 'run']
