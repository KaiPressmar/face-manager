"""In-process publish/subscribe hub that pushes live state to UI clients.

Background worker threads (import queue, autocluster queue, thumbnail warmup)
publish state changes through :class:`EventHub`; the async ``/api/events``
Server-Sent Events endpoint fans those out to every connected browser. This
replaces the timer-based polling the frontend previously used.

The hub bridges threads and asyncio: publishers run on plain worker threads
while subscribers are async generators driven by the event loop. Delivery is
scheduled onto the loop with :meth:`asyncio.AbstractEventLoop.call_soon_threadsafe`.
Each subscriber coalesces per topic (keeps only the latest payload per topic),
so frequent progress ticks can never back up a slow client.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator, Optional

logger = logging.getLogger("face_manager.events")

# Seconds of silence after which the stream emits a keepalive comment. Keeps
# intermediaries and the browser from treating an idle connection as dead.
HEARTBEAT_SECONDS = 15.0


def format_sse(topic: str, data: object) -> str:
    """Serialize one payload as a named Server-Sent Event frame.

    Args:
        topic: Event name the client listens for (``event:`` field).
        data: JSON-serializable payload placed in the ``data:`` field.

    Returns:
        A complete SSE frame terminated by a blank line.
    """
    return f"event: {topic}\ndata: {json.dumps(data, default=str)}\n\n"


class _Subscriber:
    """One connected client's coalescing mailbox.

    Only the most recent payload per topic is retained; a new payload for a
    topic already pending replaces it. ``_ready`` wakes the stream generator.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._pending: dict[str, object] = {}
        self._ready = asyncio.Event()

    def offer(self, topic: str, data: object) -> None:
        """Queue ``data`` for ``topic`` from any thread and wake the stream."""

        def apply() -> None:
            self._pending[topic] = data
            self._ready.set()

        self._loop.call_soon_threadsafe(apply)

    async def drain(self) -> list[tuple[str, object]]:
        """Wait for at least one pending payload, then take all of them."""
        await self._ready.wait()
        self._ready.clear()
        items = list(self._pending.items())
        self._pending.clear()
        return items


class EventHub:
    """Thread-safe broadcaster of coalesced, per-topic state snapshots."""

    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._subscribers: set[_Subscriber] = set()
        # Latest payload per topic, replayed to every newly connected client so
        # it starts with current state instead of an initial fetch.
        self._latest: dict[str, object] = {}

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Record the running event loop used to schedule deliveries."""
        self._loop = loop

    def publish(self, topic: str, data: object) -> None:
        """Broadcast ``data`` on ``topic`` to all subscribers.

        Safe to call from any thread. No-op until :meth:`bind_loop` has run
        (i.e. before the server has fully started).

        Args:
            topic: Logical channel, e.g. ``"imports"`` or ``"clusters"``.
            data: JSON-serializable snapshot or invalidation payload.
        """
        self._latest[topic] = data
        loop = self._loop
        if loop is None:
            return
        for subscriber in list(self._subscribers):
            subscriber.offer(topic, data)

    async def stream(self) -> AsyncIterator[str]:
        """Yield SSE frames for one client until it disconnects.

        Immediately replays the latest payload for every known topic, then
        streams subsequent updates, emitting a keepalive comment when idle.
        """
        loop = asyncio.get_running_loop()
        self.bind_loop(loop)
        subscriber = _Subscriber(loop)
        self._subscribers.add(subscriber)
        try:
            for topic, data in list(self._latest.items()):
                yield format_sse(topic, data)
            while True:
                try:
                    items = await asyncio.wait_for(
                        subscriber.drain(), timeout=HEARTBEAT_SECONDS
                    )
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                for topic, data in items:
                    yield format_sse(topic, data)
        except asyncio.CancelledError:
            raise
        finally:
            self._subscribers.discard(subscriber)


# Process-wide singleton shared by the queue services and the API layer.
event_hub = EventHub()
