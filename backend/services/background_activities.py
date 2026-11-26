"""Publish current background activity status for the desktop activity panel."""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass
from typing import Literal

from .events import EventHub, TrailingThrottle, event_hub

ActivityStatus = Literal["running", "completed", "failed", "cancelled"]


@dataclass(frozen=True)
class BackgroundActivity:
    id: str
    kind: str
    status: ActivityStatus
    processed: int
    total: int
    unavailable: int
    finished_at: str | None = None


class BackgroundActivities:
    """Retain the latest activity for each source and coalesce progress ticks."""

    def __init__(self, hub: EventHub, progress_interval: float = 0.35) -> None:
        self._hub = hub
        self._lock = threading.Lock()
        self._activities: dict[str, BackgroundActivity] = {}
        self._published: tuple[BackgroundActivity, ...] = ()
        self._publish_progress = TrailingThrottle(self._publish, interval=progress_interval)

    def update(self, activity: BackgroundActivity) -> None:
        with self._lock:
            previous = self._activities.get(activity.kind)
            self._activities[activity.kind] = activity
        if previous is None or previous.id != activity.id or activity.status != "running":
            self._publish()
        else:
            self._publish_progress()

    def discard(self, kind: str, activity_id: str | None = None) -> None:
        with self._lock:
            current = self._activities.get(kind)
            if current is None or (activity_id is not None and current.id != activity_id):
                return
            del self._activities[kind]
        self._publish()

    def _publish(self) -> None:
        with self._lock:
            current = tuple(self._activities.values())
            if current == self._published:
                return
            self._hub.publish("background-activities", {"activities": [asdict(activity) for activity in current]})
            self._published = current


background_activities = BackgroundActivities(event_hub)
