"""Revision fences for the design-to-render pipeline.

A design revision is a monotonic, session-local generation counter.  It is
deliberately independent of ``meta.version`` and ``derived.checksum``: both of
those live inside a spec document, can be restored from history, and are not
safe ordering tokens.

Every committed modification increments the revision.  Deferred work (agent
shadow runs, staged approvals, proposals and candidate applies) records the
revision it was based on and is rejected when that revision is no longer live.
"""

from __future__ import annotations


class RevisionConflict(RuntimeError):
    """Raised when deferred work is based on a superseded design revision."""

    def __init__(self, expected, current):
        self.expected = expected
        self.current = current
        super().__init__(
            f"design changed since this request was prepared "
            f"(expected revision {expected}, current revision {current})")


class RenderGenerationError(RuntimeError):
    """Raised when a new design cannot be solved, built or verified."""
