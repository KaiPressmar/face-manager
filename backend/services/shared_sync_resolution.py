"""Portable conflict decisions and durable compare-and-set receipts.

Callers authenticate the share and author, supply the current decision heads,
and own the transaction that changes any live face or person assignment.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Literal

from .shared_sync_journal import digest, encode
from .shared_sync_merge import Assignment
from .shared_sync_reconcile import _assignment


_SHA256 = re.compile(r"[0-9a-f]{64}")
_MANIFEST_ID = re.compile(r"[0-9a-f]{32}")
_MAX_PARENTS = 32
ResolutionStatus = Literal["ready", "applied", "stale", "concurrent"]


def _identifier(value: str, pattern: re.Pattern = _SHA256) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError("Invalid shared conflict identifier")
    return value


def shared_library_scope(manifest: dict) -> str:
    """Name a verified share generation without local paths or device IDs."""
    if not isinstance(manifest, dict) or set(manifest) != {"version", "scope", "generation"}:
        raise ValueError("Invalid shared library manifest")
    if type(manifest["version"]) is not int or manifest["version"] != 1:
        raise ValueError("Unsupported shared library manifest version")
    return digest([
        "shared-library-scope-v1",
        _identifier(manifest["scope"], _MANIFEST_ID),
        _identifier(manifest["generation"], _MANIFEST_ID),
    ])


@dataclass(frozen=True, slots=True)
class BranchDecision:
    revision: str
    decision: Assignment | None

    def __post_init__(self) -> None:
        _identifier(self.revision)
        if self.decision is not None:
            if not isinstance(self.decision, Assignment):
                raise ValueError("Invalid conflict branch decision")
            _assignment(asdict(self.decision))


def _heads(items: Iterable[BranchDecision], *, minimum: int = 0) -> tuple[BranchDecision, ...]:
    heads = []
    revisions = set()
    for item in items:
        if len(heads) == _MAX_PARENTS or not isinstance(item, BranchDecision) or item.revision in revisions:
            raise ValueError("Invalid or duplicate conflict branches")
        heads.append(item)
        revisions.add(item.revision)
    if len(heads) < minimum:
        raise ValueError("A conflict resolution needs at least two different branches")
    return tuple(sorted(heads, key=lambda head: head.revision))


@dataclass(frozen=True, slots=True)
class ConflictResolution:
    library_scope: str
    photo_sha256: str
    subject_id: str
    parents: tuple[BranchDecision, ...]
    selected_revision: str

    def __post_init__(self) -> None:
        for value in (self.library_scope, self.photo_sha256, self.subject_id, self.selected_revision):
            _identifier(value)
        object.__setattr__(self, "parents", _heads(self.parents, minimum=2))
        # A fresh detection is not an explicit instruction to clear a decision.
        if not any(head.revision == self.selected_revision and head.decision is not None for head in self.parents):
            raise ValueError("Select an explicit decision from the conflict branches")

    @property
    def event_id(self) -> str:
        return digest(_wire_body(self))

    @property
    def chosen_decision(self) -> Assignment:
        for head in self.parents:
            if head.revision == self.selected_revision and head.decision is not None:
                return head.decision
        raise ValueError("A conflict resolution must choose an explicit decision")

    def as_branch(self) -> BranchDecision:
        """Make the chosen decision a causal parent of any subsequent change."""
        return BranchDecision(self.event_id, self.chosen_decision)


def _wire_body(event: ConflictResolution) -> dict:
    return {
        "type": "conflict_resolution",
        "version": 1,
        "library_scope": event.library_scope,
        "photo_sha256": event.photo_sha256,
        "subject_id": event.subject_id,
        "parents": [asdict(head) for head in event.parents],
        "selected_revision": event.selected_revision,
    }


def encode_resolution(event: ConflictResolution) -> dict:
    return {**_wire_body(event), "event_id": event.event_id}


def decode_resolution(payload: dict) -> ConflictResolution:
    """Verify wire data and its order-independent event identity."""
    if not isinstance(payload, dict) or set(payload) != {
        "type", "version", "library_scope", "photo_sha256", "subject_id",
        "parents", "selected_revision", "event_id",
    }:
        raise ValueError("Invalid conflict event")
    if payload["type"] != "conflict_resolution" or type(payload["version"]) is not int or payload["version"] != 1:
        raise ValueError("Unsupported conflict event version")
    values = payload["parents"]
    if not isinstance(values, list) or not 2 <= len(values) <= _MAX_PARENTS:
        raise ValueError("Invalid conflict parent count")
    parents = []
    for head in values:
        if not isinstance(head, dict) or set(head) != {"revision", "decision"}:
            raise ValueError("Invalid conflict branch")
        parents.append(BranchDecision(head["revision"], _assignment(head["decision"])))
    event = ConflictResolution(
        payload["library_scope"], payload["photo_sha256"], payload["subject_id"],
        tuple(parents), payload["selected_revision"],
    )
    if event.event_id != payload["event_id"]:
        raise ValueError("The conflict event does not match its identity")
    return event


def _require_transaction(conn: sqlite3.Connection) -> None:
    if not conn.in_transaction:
        raise ValueError("A caller-owned database transaction is required")


def initialize_resolutions(conn: sqlite3.Connection) -> None:
    """Create the local receipt storage within the caller's transaction."""
    _require_transaction(conn)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS shared_sync_resolution (
            event_id TEXT PRIMARY KEY,
            library_scope TEXT NOT NULL,
            photo_sha256 TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            payload TEXT NOT NULL,
            applied INTEGER NOT NULL DEFAULT 0 CHECK (applied IN (0,1))
        ) WITHOUT ROWID
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS shared_sync_resolution_parent (
            event_id TEXT NOT NULL REFERENCES shared_sync_resolution(event_id) ON DELETE CASCADE,
            revision TEXT NOT NULL,
            decision TEXT NOT NULL,
            PRIMARY KEY (event_id, revision)
        ) WITHOUT ROWID
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS shared_sync_resolution_parent_revision
        ON shared_sync_resolution_parent(revision, event_id)
    """)


def record_resolution(conn: sqlite3.Connection, event: ConflictResolution) -> bool:
    """Persist one authenticated event even if its parents have not arrived yet."""
    initialize_resolutions(conn)
    wire = encode_resolution(event)
    event_id = wire["event_id"]
    inserted = conn.execute("""
        INSERT INTO shared_sync_resolution (event_id, library_scope, photo_sha256, subject_id, payload)
        VALUES (?, ?, ?, ?, ?) ON CONFLICT (event_id) DO NOTHING
    """, (event_id, event.library_scope, event.photo_sha256, event.subject_id, encode(wire))).rowcount
    if inserted:
        conn.executemany(
            "INSERT INTO shared_sync_resolution_parent (event_id, revision, decision) VALUES (?, ?, ?)",
            ((event_id, head["revision"], encode(head["decision"])) for head in wire["parents"]),
        )
    return bool(inserted)


def concurrent_resolutions(conn: sqlite3.Connection, event: ConflictResolution) -> tuple[ConflictResolution, ...]:
    """Return unresolved competing choices for the exact same reviewed branches."""
    _require_transaction(conn)
    rows = conn.execute("""
        SELECT DISTINCT other.event_id, other.payload
        FROM shared_sync_resolution_parent own
        JOIN shared_sync_resolution_parent overlap ON overlap.revision = own.revision
        JOIN shared_sync_resolution other ON other.event_id = overlap.event_id
        WHERE own.event_id = ? AND other.event_id != own.event_id
          AND other.library_scope = ? AND other.photo_sha256 = ? AND other.subject_id = ?
          AND NOT EXISTS (
              SELECT revision, decision FROM shared_sync_resolution_parent WHERE event_id = own.event_id
              EXCEPT
              SELECT revision, decision FROM shared_sync_resolution_parent WHERE event_id = other.event_id
          )
          AND NOT EXISTS (
              SELECT revision, decision FROM shared_sync_resolution_parent WHERE event_id = other.event_id
              EXCEPT
              SELECT revision, decision FROM shared_sync_resolution_parent WHERE event_id = own.event_id
          )
          AND NOT EXISTS (
              SELECT 1 FROM shared_sync_resolution_parent successor_parent
              JOIN shared_sync_resolution successor ON successor.event_id = successor_parent.event_id
              WHERE successor_parent.revision = other.event_id AND successor.applied = 1
                AND successor.library_scope = other.library_scope
                AND successor.photo_sha256 = other.photo_sha256 AND successor.subject_id = other.subject_id
          )
        ORDER BY other.event_id
    """, (event.event_id, event.library_scope, event.photo_sha256, event.subject_id))
    return tuple(decode_resolution(json.loads(row[1])) for row in rows)


def assess_resolution(
    conn: sqlite3.Connection, event: ConflictResolution, current_parents: Iterable[BranchDecision],
) -> ResolutionStatus:
    """Match every parent decision and expose competing unresolved resolutions."""
    _require_transaction(conn)
    stored = conn.execute(
        "SELECT applied FROM shared_sync_resolution WHERE event_id = ?", (event.event_id,),
    ).fetchone()
    if stored is None:
        raise ValueError("The conflict event must be recorded before assessing it")
    if concurrent_resolutions(conn, event):
        return "concurrent"
    if stored[0]:
        return "applied"
    return "ready" if _heads(current_parents) == event.parents else "stale"


def mark_resolution_applied(
    conn: sqlite3.Connection, event: ConflictResolution, current_parents: Iterable[BranchDecision],
) -> bool:
    """Record an application once, atomically with the caller's live mutation.

    Use BEGIN IMMEDIATE and re-read the actual heads in that transaction. If
    changing the live assignment fails, roll back the receipt with that change.
    """
    if assess_resolution(conn, event, current_parents) != "ready":
        return False
    return bool(conn.execute(
        "UPDATE shared_sync_resolution SET applied = 1 WHERE event_id = ? AND applied = 0",
        (event.event_id,),
    ).rowcount)
