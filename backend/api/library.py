"""Coordinate API operations with replacement of the active library."""

import threading

from fastapi import HTTPException

from ..services.events import publish_cluster_change

# Serializes database replacement with cluster mutations and album writes.
write_lock = threading.RLock()
# Also guards pending image geometry in the API during a library change.
state_lock = threading.Lock()
restoring = False
generation = 0


def get_generation(expected: int | None = None) -> int:
    with state_lock:
        if restoring or (
            expected is not None and expected != generation
        ):
            raise HTTPException(
                status_code=409,
                detail="Die Bibliothek wird gewechselt. Bitte versuche es erneut.",
            )
        return generation


def publish_active_cluster_change(reason: str) -> None:
    with state_lock:
        if not restoring:
            publish_cluster_change(reason)
