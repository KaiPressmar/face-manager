"""Apply one verified shared photo snapshot inside the local write transaction."""

from dataclasses import replace
import sqlite3
import unicodedata

from ..db.schema import FACE_REVIEW_STATUS_ACTIVE, normalize_person_name_key
from .shared_sync_merge import Assignment, MergeConflict, MergePlan
from .shared_sync_reconcile import compare_verified_photo
from .shared_sync_snapshot import SnapshotPhoto, capture_image, snapshot_device_namespace, snapshot_face_id
from .storage import mark_persons_dirty_for_recluster


def _create_tables(connection: sqlite3.Connection) -> None:
    connection.execute("""
        CREATE TABLE IF NOT EXISTS shared_sync_person_link (
            scope TEXT NOT NULL,
            shared_person_key TEXT NOT NULL,
            person_id INTEGER UNIQUE REFERENCES person(id) ON DELETE SET NULL,
            PRIMARY KEY (scope, shared_person_key)
        )
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS shared_sync_face_receipt (
            face_id INTEGER PRIMARY KEY REFERENCES face(id) ON DELETE CASCADE,
            scope TEXT NOT NULL,
            shared_face_key TEXT NOT NULL
        )
    """)


def _scoped_rows(connection, *, scope: str, device_id: str, image_id: int):
    namespace = snapshot_device_namespace(device_id)
    rows = connection.execute(
        """SELECT face.id, face.cluster_id, face.review_status, cluster.label, cluster.person_id,
                  link.shared_person_key, receipt.face_id AS applied_face
            FROM face LEFT JOIN cluster ON cluster.id = face.cluster_id
            LEFT JOIN shared_sync_person_link link ON link.scope = ? AND link.person_id = cluster.person_id
            LEFT JOIN shared_sync_face_receipt receipt ON receipt.face_id = face.id
            WHERE face.image_id = ?""",
        (scope, image_id),
    )
    return {snapshot_face_id(namespace, row["id"]): row for row in rows}


def _mapped_snapshot(local: SnapshotPhoto, rows: dict) -> SnapshotPhoto:
    faces = []
    for face in local.faces:
        decision = face.decision
        row = rows[face.face_id]
        if decision is None and row["applied_face"] is not None:
            decision = Assignment("unassigned", provenance="confirmed")
        elif decision is not None and decision.kind == "person" and row["shared_person_key"] is not None:
            decision = replace(decision, person_key=row["shared_person_key"])
        faces.append(replace(face, decision=decision) if face.decision is not decision else face)
    return replace(local, faces=tuple(faces))


def _has_links(connection) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'shared_sync_person_link'"
    ).fetchone() is not None


def scoped_snapshot(connection, *, scope: str, device_id: str, local: SnapshotPhoto) -> SnapshotPhoto:
    """Retain shared identities when comparing or publishing current local decisions.

    Use the same caller-owned transaction that captured the local snapshot.
    """
    if not connection.in_transaction:
        raise ValueError("Mapping shared identities requires a caller-owned transaction")
    if not _has_links(connection):
        return local
    rows = _scoped_rows(connection, scope=scope, device_id=device_id, image_id=local.image_id)
    return _mapped_snapshot(local, rows)


def compare_for_scope(connection, *, scope: str, device_id: str, local: SnapshotPhoto,
                      remote: dict, verified_hash: str) -> MergePlan:
    """Compare verified evidence using the receiving device's persisted identity links."""
    local = scoped_snapshot(connection, scope=scope, device_id=device_id, local=local)
    plan = compare_verified_photo(local, remote, verified_hash=verified_hash)
    available = _person_options(connection, scope, plan.carry_over, has_links=_has_links(connection))
    carry_over, conflicts = [], list(plan.conflicts)
    for match in plan.carry_over:
        if _proposes_shared_person(match) and match.decision.person_key not in available:
            conflicts.append(MergeConflict(
                "unverified_person_identity", local.photo_sha256, (match.local,), (match.shared,),
            ))
        else:
            carry_over.append(match)
    return replace(plan, carry_over=tuple(carry_over), conflicts=tuple(conflicts))


def _name_key(value: str) -> str:
    return unicodedata.normalize("NFC", normalize_person_name_key(value))


def _proposes_shared_person(match) -> bool:
    return match.decision_source == "shared" and match.decision is not None and match.decision.kind == "person"


def _person_options(connection, scope: str, candidates, *, has_links: bool) -> dict[str, int | None]:
    """Return safe identities. None means a new person may be created for that key."""
    names = {}
    by_name = {}
    for match in candidates:
        if not _proposes_shared_person(match) or match.decision.person_key is None:
            continue
        key = match.decision.person_key
        name = (match.decision.person_name or "").strip()
        name_key = _name_key(name) if not any(ord(character) < 32 for character in name) else ""
        names.setdefault(key, set()).add(name_key)
        by_name.setdefault(name_key, set()).add(key)
    if not names:
        return {}
    links = {}
    if has_links:
        placeholders = ", ".join("?" for _ in names)
        links = {
            row["shared_person_key"]: row["person_id"] for row in connection.execute(
                f"SELECT shared_person_key, person_id FROM shared_sync_person_link "
                f"WHERE scope = ? AND shared_person_key IN ({placeholders})", (scope, *names),
            )
        }
    # A deleted local identity leaves a tombstone. Replaying its shared key cannot recreate it.
    available = {key: person_id for key, person_id in links.items() if person_id is not None}
    if len(links) < len(names):
        occupied = {_name_key(row["name"]) for row in connection.execute("SELECT name FROM person")}
        for key, alternatives in names.items():
            if key not in links and len(alternatives) == 1:
                name_key, = alternatives
                if name_key and name_key not in occupied and len(by_name[name_key]) == 1:
                    available[key] = None
    return available


def apply_unambiguous(connection, *, scope: str, source_path: str, device_id: str, image_id: int,
                      remote: dict, verified_hash: str, verified_relative_path: str) -> int:
    """Apply uniquely matched shared decisions to locally undecided faces only.

    The caller holds BEGIN IMMEDIATE, has verified the given relative path in
    the scope at the filesystem, and has selected one authoritative snapshot.
    The caller owns cache/event invalidation after its transaction commits.
    """
    if not connection.in_transaction or not connection.execute("PRAGMA foreign_keys").fetchone()[0]:
        raise ValueError("A caller-owned write transaction with foreign keys is required")
    if not isinstance(scope, str) or not scope.strip():
        raise ValueError("A verified shared library scope is required")
    local = capture_image(connection, source_path, device_id, image_id)
    remote_paths = remote.get("relative_paths")
    if (
        local is None or local.photo_sha256 != verified_hash or verified_relative_path not in local.relative_paths
        or not isinstance(remote_paths, list) or verified_relative_path not in remote_paths
    ):
        return 0

    _create_tables(connection)
    rows = _scoped_rows(connection, scope=scope, device_id=device_id, image_id=image_id)
    local = _mapped_snapshot(local, rows)
    plan = compare_verified_photo(local, remote, verified_hash=verified_hash)
    candidates = [
        match for match in plan.carry_over
        if match.decision_source == "shared" and match.decision is not None
        and rows[match.local.face_id]["review_status"] == FACE_REVIEW_STATUS_ACTIVE
        and rows[match.local.face_id]["person_id"] is None and rows[match.local.face_id]["label"] is None
    ]
    if not candidates:
        return 0

    people = _person_options(connection, scope, plan.carry_over, has_links=True)
    clusters = {}
    emptied_clusters = set()
    touched_people = set()
    applied = 0
    changed_faces = False
    for match in candidates:
        decision = match.decision
        row = rows[match.local.face_id]
        if decision.kind != "unassigned":
            person_id, status = None, decision.archive_status
            if decision.kind == "person":
                if decision.person_key not in people:
                    continue
                person_id = people[decision.person_key]
                if person_id is None:
                    person_id = connection.execute(
                        "INSERT INTO person(name) VALUES (?)", (decision.person_name.strip(),)
                    ).lastrowid
                    connection.execute(
                        "INSERT INTO shared_sync_person_link VALUES (?, ?, ?)",
                        (scope, decision.person_key, person_id),
                    )
                    people[decision.person_key] = person_id
                status = FACE_REVIEW_STATUS_ACTIVE
                touched_people.add(person_id)
            cluster_key = (person_id, status)
            if cluster_key not in clusters:
                clusters[cluster_key] = connection.execute(
                    "INSERT INTO cluster(person_id) VALUES (?)", (person_id,)
                ).lastrowid
            connection.execute(
                "UPDATE face SET cluster_id = ?, review_status = ? WHERE id = ?",
                (clusters[cluster_key], status, row["id"]),
            )
            if row["cluster_id"] is not None:
                emptied_clusters.add(row["cluster_id"])
            changed_faces = True
        connection.execute(
            "INSERT INTO shared_sync_face_receipt VALUES (?, ?, ?)", (row["id"], scope, match.shared.face_id)
        )
        applied += 1

    if changed_faces:
        connection.executemany(
            "DELETE FROM cluster WHERE id = ? AND NOT EXISTS (SELECT 1 FROM face WHERE cluster_id = cluster.id)",
            [(cluster_id,) for cluster_id in emptied_clusters],
        )
        mark_persons_dirty_for_recluster(connection, touched_people)
    return applied
