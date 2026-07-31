"""Undo and redo, as diffs against the voxel dict.

A step is `{cell: hue it had before, or None if it was empty}` — everything the
editor does is some set of cells taking new values, so one shape covers placing,
deleting, painting, filling and even loading a different model.

Diffs rather than snapshots because of the cell size. At a quarter of a metre a
plain house is 24,000 voxels; a hundred snapshots of that is hundreds of
megabytes, while a hundred brush strokes is a few thousand cells. The budget
below is in *cells* for the same reason: capping the number of steps would let
one "fill holes" over a large model quietly cost more than a thousand strokes.
"""


class History:
    """Bounded undo/redo over a voxel dict, oldest dropped first."""

    def __init__(self, max_cells=2_000_000, max_steps=256):
        self.max_cells = max_cells
        self.max_steps = max_steps
        self.done = []
        self.undone = []

    def __len__(self):
        return len(self.done)

    @property
    def cells(self):
        return sum(len(d) for d in self.done) + sum(len(d) for d in self.undone)

    def clear(self):
        self.done.clear()
        self.undone.clear()

    def push(self, before):
        """Record a step. `before` is what the changed cells held beforehand.

        A new edit invalidates the redo stack, which is what every editor does
        and what stops redo replaying a future that no longer follows.
        """
        if not before:
            return
        self.done.append(dict(before))
        self.undone.clear()
        self._trim()

    def _trim(self):
        while len(self.done) > self.max_steps or (
                self.cells > self.max_cells and len(self.done) > 1):
            self.done.pop(0)

    def undo(self, voxels):
        """Step back. Returns the number of cells changed, or 0 if there is none."""
        return self._move(voxels, self.done, self.undone)

    def redo(self, voxels):
        return self._move(voxels, self.undone, self.done)

    @staticmethod
    def _move(voxels, source, sink):
        if not source:
            return 0
        diff = source.pop()
        sink.append(apply(voxels, diff))
        return len(diff)


def apply(voxels, diff):
    """Set or clear each cell in `diff`, returning the diff that undoes it."""
    inverse = {}
    for cell, hue in diff.items():
        inverse[cell] = voxels.get(cell)
        if hue is None:
            voxels.pop(cell, None)
        else:
            voxels[cell] = hue
    return inverse


def changes(voxels, wanted):
    """The subset of `wanted` that would actually change something.

    Filtering here keeps no-op strokes off the undo stack — painting a wall the
    colour it already is should not cost you a step of history.
    """
    return {c: h for c, h in wanted.items() if voxels.get(c) != h}
