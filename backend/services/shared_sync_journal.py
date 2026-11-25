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
                CREATE TABLE IF NOT EXISTS shared_publication_receipt (
                    scope_id TEXT NOT NULL,
                    batch_number INTEGER NOT NULL,
                    rows_json TEXT NOT NULL,
                    attempted INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (scope_id, batch_number)
                );
                CREATE TABLE IF NOT EXISTS shared_local_producer (
                    scope_id TEXT NOT NULL,
                    producer TEXT NOT NULL,
                    PRIMARY KEY (scope_id, producer)
                );
                CREATE TABLE IF NOT EXISTS shared_abandoned_batch (
                    scope_id TEXT NOT NULL,
                    producer TEXT NOT NULL,
                    batch_number INTEGER NOT NULL,
                    first_sequence INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY (scope_id, producer, batch_number)
                );
                CREATE TABLE IF NOT EXISTS shared_latest_photo (
                    scope_id TEXT NOT NULL,
                    producer TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    batch_number INTEGER NOT NULL,
                    position INTEGER NOT NULL,
                    PRIMARY KEY (scope_id, producer, content_hash)
                );
                CREATE INDEX IF NOT EXISTS shared_photo_batch
                    ON shared_latest_photo(scope_id, producer, batch_number, position);
            """)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(shared_publication_receipt)")}
            if "attempted" not in columns:
                connection.execute("ALTER TABLE shared_publication_receipt ADD COLUMN attempted INTEGER NOT NULL DEFAULT 1")
            connection.execute("INSERT OR IGNORE INTO shared_local_producer SELECT id, producer FROM shared_scope")
            if connection.execute("PRAGMA user_version").fetchone()[0] < 1:
                for row in connection.execute("SELECT * FROM shared_batch WHERE digest IS NOT NULL ORDER BY batch_number"):
                    self._index_photos(connection, row["scope_id"], row["producer"], row["batch_number"], json.loads(row["payload"]))
                connection.execute("PRAGMA user_version = 1")

    @staticmethod
    def _scope(connection: sqlite3.Connection, scope_id: str) -> sqlite3.Row:
        connection.execute(
            "INSERT OR IGNORE INTO shared_scope(id, producer) VALUES (?, ?)",
            (scope_id, uuid.uuid4().hex),
        )
        scope = connection.execute("SELECT * FROM shared_scope WHERE id = ?", (scope_id,)).fetchone()
        connection.execute("INSERT OR IGNORE INTO shared_local_producer VALUES (?, ?)", (scope_id, scope["producer"]))
        return scope

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

    def publications_pending(self) -> bool:
        if not self.path.exists():
            return False
        with closing(self._connect()) as connection:
            if connection.execute("SELECT 1 FROM shared_batch WHERE digest IS NULL LIMIT 1").fetchone():
                return True
            schema = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'shared_publication_receipt' AND type = 'table'"
            ).fetchone()
            return bool(schema and connection.execute("SELECT 1 FROM shared_publication_receipt LIMIT 1").fetchone())

    def rotate_producers(self) -> None:
        """Retain the witnessed history while assigning a restored library a new sender."""
        if not self.path.exists():
            return
        self.initialize()
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            pending = connection.execute("SELECT 1 FROM shared_batch WHERE digest IS NULL LIMIT 1").fetchone()
            receipts = connection.execute(
                "SELECT 1 FROM shared_publication_receipt LIMIT 1"
            ).fetchone()
            if receipts or pending:
                raise SharedStateChanged("Reconcile the previous local publication before rotating identity")
            for row in connection.execute("SELECT id FROM shared_scope").fetchall():
                scope_id, producer = row["id"], uuid.uuid4().hex
                connection.execute("UPDATE shared_scope SET producer = ? WHERE id = ?", (producer, scope_id))
                connection.execute("INSERT INTO shared_local_producer VALUES (?, ?)", (scope_id, producer))

    def stage(self, scope_id: str, events: list[dict], *, local_rows: tuple[tuple[int, int], ...] = ()) -> None:
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
            if local_rows:
                connection.execute(
                    "INSERT INTO shared_publication_receipt(scope_id, batch_number, rows_json) VALUES (?, ?, ?)",
                    (scope_id, batch_number, encode(local_rows)),
                )

    def pending_local_rows(self, scope_id: str, batch_number: int) -> tuple[bool, list] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT attempted, rows_json FROM shared_publication_receipt WHERE scope_id = ? AND batch_number = ?",
                (scope_id, batch_number),
            ).fetchone()
            return (bool(row["attempted"]), json.loads(row["rows_json"])) if row else None

    def mark_attempted(self, scope_id: str, batch_number: int) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "UPDATE shared_publication_receipt SET attempted = 1 WHERE scope_id = ? AND batch_number = ?",
                (scope_id, batch_number),
            )

    def discard_unsent(self, scope_id: str, batch_number: int) -> None:
        with closing(self._connect()) as connection, connection:
            scope = self._scope(connection, scope_id)
            removed = connection.execute(
                "DELETE FROM shared_publication_receipt WHERE scope_id = ? AND batch_number = ? AND attempted = 0",
                (scope_id, batch_number),
            ).rowcount
            if removed != 1:
                raise SharedStateChanged("The publication may already have been attempted")
            connection.execute(
                "DELETE FROM shared_batch WHERE scope_id = ? AND producer = ? AND batch_number = ? AND digest IS NULL",
                (scope_id, scope["producer"], batch_number),
            )

    def abandon_attempted(self, scope_id: str, batch_number: int) -> None:
        """Retire the sender without replacing an uncertain immutable slot."""
        with closing(self._connect()) as connection, connection:
            scope = self._scope(connection, scope_id)
            producer = scope["producer"]
            pending = self._latest(connection, scope_id, producer)
            if pending is None or pending["batch_number"] != batch_number or pending["digest"] is not None:
                raise SharedStateChanged("The pending publication has changed")
            connection.execute(
                "INSERT INTO shared_abandoned_batch VALUES (?, ?, ?, ?, ?)",
                (scope_id, producer, batch_number, pending["first_sequence"], pending["payload"]),
            )
            connection.execute(
                "DELETE FROM shared_batch WHERE scope_id = ? AND producer = ? AND batch_number = ?",
                (scope_id, producer, batch_number),
            )
            connection.execute(
                "DELETE FROM shared_publication_receipt WHERE scope_id = ? AND batch_number = ?", (scope_id, batch_number),
            )
            producer = uuid.uuid4().hex
            connection.execute("UPDATE shared_scope SET producer = ? WHERE id = ?", (producer, scope_id))
            connection.execute("INSERT INTO shared_local_producer VALUES (?, ?)", (scope_id, producer))

    def acknowledged_local_rows(self, scope_id: str) -> tuple[int, list] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """SELECT r.batch_number, r.rows_json FROM shared_publication_receipt r
                   JOIN shared_scope s ON s.id = r.scope_id
                   JOIN shared_batch b ON b.scope_id = r.scope_id AND b.producer = s.producer
                       AND b.batch_number = r.batch_number AND b.digest IS NOT NULL
                   WHERE r.scope_id = ? ORDER BY r.batch_number LIMIT 1""",
                (scope_id,),
            ).fetchone()
            return (row["batch_number"], json.loads(row["rows_json"])) if row else None

    def forget_local_receipt(self, scope_id: str, batch_number: int) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "DELETE FROM shared_publication_receipt WHERE scope_id = ? AND batch_number = ?", (scope_id, batch_number)
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
                "local_producers": [row[0] for row in connection.execute(
                    """SELECT p.producer FROM shared_local_producer p JOIN shared_scope s ON s.id = p.scope_id
                       WHERE s.id = ? OR s.manifest = ?""", (scope_id, scope["manifest"]),
                )],
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
                self._index_photos(connection, scope_id, producer, batch_number, events)
                return
            if scope["producer"] == producer:
                raise SharedStateChanged("The share contains an unknown publication from this device")
            abandoned = connection.execute(
                """SELECT first_sequence, payload FROM shared_abandoned_batch
                   WHERE scope_id = ? AND producer = ? AND batch_number = ?""",
                (scope_id, producer, batch_number),
            ).fetchone()
            if abandoned and (abandoned["first_sequence"] != first_sequence or abandoned["payload"] != payload):
                raise SharedStateChanged("A late publication differs from the retired intent")
            latest = self._latest(connection, scope_id, producer)
            expected_number = 1 if latest is None else latest["batch_number"] + 1
            expected_sequence = 1 if latest is None else latest["first_sequence"] + latest["event_count"]
            if batch_number != expected_number or first_sequence != expected_sequence:
                raise SharedStateChanged("The shared event sequence is discontinuous")
            connection.execute(
                "INSERT INTO shared_batch VALUES (?, ?, ?, ?, ?, ?, ?)",
                (scope_id, producer, batch_number, first_sequence, len(events), payload, verified_digest),
            )
            if abandoned:
                connection.execute(
                    "DELETE FROM shared_abandoned_batch WHERE scope_id = ? AND producer = ? AND batch_number = ?",
                    (scope_id, producer, batch_number),
                )
            self._index_photos(connection, scope_id, producer, batch_number, events)

    @staticmethod
    def _index_photos(connection, scope_id: str, producer: str, batch_number: int, events: list[dict]) -> None:
        snapshots = [
            (scope_id, producer, event["photo_sha256"], batch_number, position)
            for position, event in enumerate(events)
            if event.get("type") == "photo_snapshot" and isinstance(event.get("photo_sha256"), str)
            and len(event["photo_sha256"]) == 64
        ]
        connection.executemany(
            """INSERT INTO shared_latest_photo VALUES (?, ?, ?, ?, ?)
               ON CONFLICT (scope_id, producer, content_hash) DO UPDATE SET
                   batch_number = excluded.batch_number, position = excluded.position
               WHERE (excluded.batch_number, excluded.position) >
                   (shared_latest_photo.batch_number, shared_latest_photo.position)""",
            snapshots,
        )

    def latest_photo_positions(self, scope_id: str, producer: str, batch_number: int) -> set[int]:
        with closing(self._connect()) as connection:
            return {
                row[0] for row in connection.execute(
                    "SELECT position FROM shared_latest_photo WHERE scope_id = ? AND producer = ? AND batch_number = ?",
                    (scope_id, producer, batch_number),
                )
            }

    def has_current_photo(self, scope_id: str, producer: str, event: dict) -> bool:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """SELECT p.position, b.payload FROM shared_latest_photo p
                   JOIN shared_batch b ON b.scope_id = p.scope_id AND b.producer = p.producer
                       AND b.batch_number = p.batch_number
                   WHERE p.scope_id = ? AND p.producer = ? AND p.content_hash = ?""",
                (scope_id, producer, event["photo_sha256"]),
            ).fetchone()
            return bool(row and json.loads(row["payload"])[row["position"]] == event)

    def events(self, scope_id: str, producer: str, batch_number: int) -> list[dict]:
        """Read verified events from a specific producer batch for application."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                """SELECT payload FROM shared_batch
                   WHERE scope_id = ? AND producer = ? AND batch_number = ? AND digest IS NOT NULL""",
                (scope_id, producer, batch_number),
            ).fetchone()
            return json.loads(row["payload"]) if row is not None else []
