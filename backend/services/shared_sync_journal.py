"""Device-local checkpoints for an immutable shared event log.

The journal is a separate database on the device. Restoring the photo library
does not erase the last share state that this device has verified.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

from .shared_sync_transport import MAX_EVENT_BATCH_BYTES, MAX_EVENTS_PER_BATCH


def _valid_batch(events, payload: str) -> bool:
    return (
        isinstance(events, list) and 1 <= len(events) <= MAX_EVENTS_PER_BATCH
        and all(isinstance(event, dict) for event in events)
        and len(payload.encode("utf-8")) <= MAX_EVENT_BATCH_BYTES
    )


def encode(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value) -> str:
    return hashlib.sha256(encode(value).encode("utf-8")).hexdigest()


class SharedStateChanged(ValueError):
    pass


class ExchangeJournal:
    """Keep the exact outgoing intent until its shared slot is acknowledged."""

    def __init__(self, path: Path):
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=2)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS shared_scope (
                    id TEXT PRIMARY KEY,
                    producer TEXT UNIQUE NOT NULL,
                    manifest TEXT
                );
                CREATE TABLE IF NOT EXISTS shared_batch (
                    scope_id TEXT NOT NULL,
                    producer TEXT NOT NULL,
                    batch_number INTEGER NOT NULL,
                    first_sequence INTEGER NOT NULL,
                    event_count INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    digest TEXT,
                    PRIMARY KEY (scope_id, producer, batch_number)
                );
                CREATE INDEX IF NOT EXISTS shared_pending ON shared_batch(scope_id, producer)
                    WHERE digest IS NULL;
            """)

    @staticmethod
    def _scope(connection: sqlite3.Connection, scope_id: str) -> sqlite3.Row:
        connection.execute(
            "INSERT OR IGNORE INTO shared_scope(id, producer) VALUES (?, ?)",
            (scope_id, uuid.uuid4().hex),
        )
        return connection.execute("SELECT * FROM shared_scope WHERE id = ?", (scope_id,)).fetchone()

    @staticmethod
    def _latest(connection, scope_id: str, producer: str):
        return connection.execute(
            """SELECT * FROM shared_batch WHERE scope_id = ? AND producer = ?
               ORDER BY batch_number DESC LIMIT 1""",
            (scope_id, producer),
        ).fetchone()

    def bind(self, scope_id: str, manifest: dict) -> None:
        serialized = encode(manifest)
        with closing(self._connect()) as connection, connection:
            scope = self._scope(connection, scope_id)
            if scope["manifest"] is not None and scope["manifest"] != serialized:
                raise SharedStateChanged("The shared library identity has changed")
            connection.execute("UPDATE shared_scope SET manifest = ? WHERE id = ?", (serialized, scope_id))

    def stage(self, scope_id: str, events: list[dict]) -> None:
        payload = encode(events)
        if not _valid_batch(events, payload):
            raise ValueError("The outgoing event batch is outside the supported size")
        with closing(self._connect()) as connection, connection:
            scope = self._scope(connection, scope_id)
            if scope["manifest"] is None:
                raise ValueError("The shared library identity is not yet verified")
            latest = self._latest(connection, scope_id, scope["producer"])
            if latest is not None and latest["digest"] is None:
                if latest["payload"] != payload:
                    raise SharedStateChanged("Reconcile the pending batch before staging a new one")
                return
            batch_number = 1 if latest is None else latest["batch_number"] + 1
            first_sequence = 1 if latest is None else latest["first_sequence"] + latest["event_count"]
            connection.execute(
                "INSERT INTO shared_batch VALUES (?, ?, ?, ?, ?, ?, NULL)",
                (scope_id, scope["producer"], batch_number, first_sequence, len(events), payload),
            )

    def checkpoint(self, scope_id: str) -> dict:
        with closing(self._connect()) as connection, connection:
            scope = self._scope(connection, scope_id)
            rows = connection.execute(
                """SELECT b.producer, b.batch_number, b.digest FROM shared_batch AS b
                   WHERE scope_id = ? AND digest IS NOT NULL AND batch_number = (
                       SELECT MAX(batch_number) FROM shared_batch WHERE scope_id = b.scope_id
                         AND producer = b.producer AND digest IS NOT NULL
                   )""",
                (scope_id,),
            ).fetchall()
            outgoing = self._latest(connection, scope_id, scope["producer"])
            return {
                "producer_id": scope["producer"],
                "known_manifest": json.loads(scope["manifest"]) if scope["manifest"] is not None else None,
                "cursor": {row["producer"]: row["batch_number"] for row in rows},
                "witnesses": {
                    row["producer"]: {"batch": row["batch_number"], "digest": row["digest"]} for row in rows
                },
                "outgoing": {
                    "batch": outgoing["batch_number"], "first_sequence": outgoing["first_sequence"],
                    "events": json.loads(outgoing["payload"]),
                } if outgoing is not None and outgoing["digest"] is None else None,
            }

    def accept(self, scope_id: str, producer: str, batch: dict, verified_digest: str) -> None:
        """Store an incoming batch or acknowledge the same durable outgoing intent."""
        if digest(batch) != verified_digest:
            raise SharedStateChanged("The shared event batch has an invalid digest")
        batch_number, first_sequence, events = batch["batch_no"], batch["first_sequence"], batch["events"]
        payload = encode(events)
        if (
            type(batch_number) is not int or type(first_sequence) is not int
            or not _valid_batch(events, payload) or batch.get("producer_id") != producer
            or batch.get("last_sequence") != first_sequence + len(events) - 1
        ):
            raise ValueError("The shared event batch has invalid counters or content")
        with closing(self._connect()) as connection, connection:
            scope = self._scope(connection, scope_id)
            existing = connection.execute(
                """SELECT * FROM shared_batch WHERE scope_id = ? AND producer = ? AND batch_number = ?""",
                (scope_id, producer, batch_number),
            ).fetchone()
            if existing is not None:
                if (existing["digest"] not in (None, verified_digest) or existing["payload"] != payload
                        or existing["first_sequence"] != first_sequence):
                    raise SharedStateChanged("A shared event slot contains a conflicting payload")
                connection.execute(
                    """UPDATE shared_batch SET digest = ? WHERE scope_id = ? AND producer = ? AND batch_number = ?""",
                    (verified_digest, scope_id, producer, batch_number),
                )
                return
            if scope["producer"] == producer:
                raise SharedStateChanged("The share contains an unknown publication from this device")
            latest = self._latest(connection, scope_id, producer)
            expected_number = 1 if latest is None else latest["batch_number"] + 1
            expected_sequence = 1 if latest is None else latest["first_sequence"] + latest["event_count"]
            if batch_number != expected_number or first_sequence != expected_sequence:
                raise SharedStateChanged("The shared event sequence is discontinuous")
            connection.execute(
                "INSERT INTO shared_batch VALUES (?, ?, ?, ?, ?, ?, ?)",
                (scope_id, producer, batch_number, first_sequence, len(events), payload, verified_digest),
            )

    def events(self, scope_id: str, producer: str, batch_number: int) -> list[dict]:
        """Read verified events from a specific producer batch for application."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                """SELECT payload FROM shared_batch
                   WHERE scope_id = ? AND producer = ? AND batch_number = ? AND digest IS NOT NULL""",
                (scope_id, producer, batch_number),
            ).fetchone()
            return json.loads(row["payload"]) if row is not None else []
