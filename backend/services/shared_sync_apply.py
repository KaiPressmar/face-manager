"""Apply one verified shared photo snapshot inside the local write transaction."""

from dataclasses import asdict, replace
import re
import sqlite3
from typing import Literal
import unicodedata
from uuid import uuid5

from ..db.schema import FACE_REVIEW_STATUS_ACTIVE, normalize_person_name_key
from .shared_sync_journal import digest
from .shared_sync_merge import Assignment, FaceEvidence, FaceMatch, MergeConflict, MergePlan
from .shared_sync_reconcile import compare_verified_photo
from .shared_sync_snapshot import SnapshotPhoto, capture_image, snapshot_device_namespace, snapshot_face_id
from .storage import mark_persons_dirty_for_recluster


_PRODUCER = re.compile(r"[0-9a-f]{32}")
_RESOLUTION_ID = re.compile(r"[0-9a-f]{64}")


class SharedConflictChanged(ValueError):
    """The confirmed photo or pair no longer matches the current conflict."""


class SharedConflictBlocked(ValueError):
    """The confirmed choice cannot safely be represented by a local identity."""


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


def _has_observations(connection: sqlite3.Connection) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'shared_sync_auto_observation'"
    ).fetchone() is not None


def _create_observations(connection: sqlite3.Connection) -> None:
    if _has_observations(connection):
        return
    connection.execute("""
        CREATE TABLE shared_sync_auto_observation (
            face_id INTEGER PRIMARY KEY REFERENCES face(id) ON DELETE CASCADE,
            scope TEXT NOT NULL,
            producer TEXT NOT NULL,
            sequence INTEGER NOT NULL,
            shared_face_key TEXT NOT NULL,
            verified_path TEXT NOT NULL,
            shared_geometry TEXT NOT NULL,
            shared_state TEXT NOT NULL,
            local_state TEXT NOT NULL
        )
    """)
    _create_invalidation_triggers(connection, "observation", "shared_sync_auto_observation")


def _create_invalidation_triggers(connection, category: str, table: str) -> None:
    # A local write to these fields ends provenance even if it writes back the
    # previous value. SQL cannot distinguish manual and background touches.
    invalidations = (
        ("face", "UPDATE OF id, image_id, cluster_id, review_status, bbox_x, bbox_y, bbox_w, bbox_h, embedding ON face",
         "SELECT OLD.id UNION SELECT NEW.id"),
        ("cluster", "UPDATE OF person_id, label ON cluster", "SELECT id FROM face WHERE cluster_id = OLD.id"),
        ("person", "UPDATE OF name ON person", "SELECT f.id FROM face f JOIN cluster c ON c.id = f.cluster_id WHERE c.person_id = OLD.id"),
        ("image", "UPDATE OF path, content_hash, raw_width, raw_height, exif_orientation, processed_at ON image",
         "SELECT id FROM face WHERE image_id = OLD.id"),
        ("location_insert", "INSERT ON image_location", "SELECT id FROM face WHERE image_id = NEW.image_id"),
        ("location_delete", "DELETE ON image_location", "SELECT id FROM face WHERE image_id = OLD.image_id"),
        ("location_update", "UPDATE OF image_id, path ON image_location",
         "SELECT id FROM face WHERE image_id IN (OLD.image_id, NEW.image_id)"),
    )
    for name, event, affected in invalidations:
        connection.execute(f"""CREATE TRIGGER IF NOT EXISTS shared_sync_invalidate_{category}_{name} AFTER {event}
            BEGIN DELETE FROM {table} WHERE face_id IN ({affected}); END""")


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


def _has_resolutions(connection) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'shared_sync_face_resolution'"
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
    if conflicts and _has_resolutions(connection):
        rows = _scoped_rows(connection, scope=scope, device_id=device_id, image_id=local.image_id)
        saved = {
            (row["face_id"], row["shared_face_key"]): row["fingerprint"] for row in connection.execute(
                """SELECT r.face_id, r.shared_face_key, r.fingerprint FROM shared_sync_face_resolution r
                   JOIN face f ON f.id = r.face_id WHERE f.image_id = ? AND r.scope = ?""",
                (local.image_id, scope),
            )
        }
        conflicts = [conflict for conflict in conflicts if not _is_resolved(conflict, rows, saved)]
    return replace(plan, carry_over=tuple(carry_over), conflicts=tuple(conflicts))


def _resolution_fingerprint(left, right) -> str:
    return digest([
        left.photo_sha256, left.bbox, asdict(left.decision) if left.decision else None,
        right.bbox, asdict(right.decision) if right.decision else None,
    ])


def _is_resolved(conflict, rows: dict, saved: dict) -> bool:
    if (conflict.reason not in ("different_decisions", "unverified_person_identity")
            or len(conflict.local) != 1 or len(conflict.shared) != 1):
        return False
    left, right = conflict.local[0], conflict.shared[0]
    recorded = saved.get((rows[left.face_id]["id"], right.face_id))
    return recorded is not None and recorded == _resolution_fingerprint(left, right)


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


def is_conflict_person_available(
    connection, *, scope: str, left: FaceEvidence | None, right: FaceEvidence | None,
) -> bool:
    """Check whether the shared choice has a usable identity without changing it."""
    if right is None or right.decision is None:
        return False
    if right.decision.kind != "person":
        return True
    if left is None:
        return False
    candidates = [FaceMatch(left, right, right.decision, "shared")]
    return right.decision.person_key in _person_options(connection, scope, candidates, has_links=_has_links(connection))


def _linked_person(connection, scope, decision, available) -> int:
    if decision.person_key not in available:
        raise SharedConflictBlocked("The shared person cannot be safely linked to a local person")
    person_id = available[decision.person_key]
    if person_id is None:
        person_id = connection.execute(
            "INSERT INTO person(name) VALUES (?)", (decision.person_name.strip(),)
        ).lastrowid
        connection.execute(
            "INSERT INTO shared_sync_person_link VALUES (?, ?, ?)",
            (scope, decision.person_key, person_id),
        )
        available[decision.person_key] = person_id
    return person_id


def _require_write_transaction(connection, scope: str) -> None:
    if not connection.in_transaction or not connection.execute("PRAGMA foreign_keys").fetchone()[0]:
        raise ValueError("A caller-owned write transaction with foreign keys is required")
    if not isinstance(scope, str) or not scope.strip():
        raise ValueError("A verified shared library scope is required")


def _capture_verified(connection, source_path, device_id, image_id, remote, verified_hash, verified_relative_path):
    local = capture_image(connection, source_path, device_id, image_id)
    remote_paths = remote.get("relative_paths")
    if (
        local is None or local.photo_sha256 != verified_hash or verified_relative_path not in local.relative_paths
        or not isinstance(remote_paths, list) or verified_relative_path not in remote_paths
    ):
        return None
    return local


def _face_geometry(face: FaceEvidence) -> str:
    return digest([face.face_id, face.photo_sha256, face.bbox])


def _face_state(face: FaceEvidence) -> str:
    return digest([_face_geometry(face), asdict(face.decision) if face.decision else None])


def _matched_pairs(plan: MergePlan):
    for match in (*plan.carry_over, *plan.equivalent):
        yield match.local, match.shared, False
    for issue in plan.conflicts:
        if (issue.reason in ("different_decisions", "unverified_person_identity")
                and len(issue.local) == len(issue.shared) == 1):
            yield issue.local[0], issue.shared[0], True


def _prior_observations(connection, scope, producer, sequence, image_id):
    return {
        row["face_id"]: row for row in connection.execute(
            """SELECT o.* FROM shared_sync_auto_observation o
               JOIN face f ON f.id = o.face_id
               WHERE o.scope = ? AND o.producer = ? AND o.sequence < ? AND f.image_id = ?""",
            (scope, producer, sequence, image_id),
        )
    }


def _remote_corrections(connection, *, plan, rows, scope, producer, sequence, image_id, relative):
    """Find still-owned, exact matches. Relinquish observations for lost evidence."""
    previous = _prior_observations(connection, scope, producer, sequence, image_id)
    updates, observed = [], {}
    if not previous:
        return updates, observed
    for left, right, conflicted in _matched_pairs(plan):
        row = rows[left.face_id]
        old = previous.get(row["id"])
        if (
            old is None or old["shared_face_key"] != right.face_id or old["verified_path"] != relative
            or old["shared_geometry"] != _face_geometry(right) or old["local_state"] != _face_state(left)
            or row["label"] is not None
        ):
            continue
        del previous[row["id"]]
        observed[left.face_id] = right
        if conflicted and right.decision is not None and old["shared_state"] != _face_state(right):
            updates.append(FaceMatch(left, right, right.decision, "shared"))
    connection.executemany("DELETE FROM shared_sync_auto_observation WHERE face_id = ?", [(key,) for key in previous])
    return updates, observed


def _save_observations(connection, *, observations, local, rows, scope, producer, sequence, relative) -> None:
    if not observations:
        return
    connection.executemany("""
        INSERT INTO shared_sync_auto_observation VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (face_id) DO UPDATE SET
            sequence = excluded.sequence, shared_state = excluded.shared_state, local_state = excluded.local_state
    """, [
        (rows[face.face_id]["id"], scope, producer, sequence, right.face_id, relative,
         _face_geometry(right), _face_state(right), _face_state(face))
        for face in local.faces if (right := observations.get(face.face_id)) is not None
    ])


def apply_unambiguous(connection, *, scope: str, source_path: str, device_id: str, image_id: int,
                      remote: dict, verified_hash: str, verified_relative_path: str,
                      remote_producer: str | None = None, remote_sequence: int | None = None) -> int:
    """Apply unique shared decisions to undecided or still remotely owned faces.

    The caller holds BEGIN IMMEDIATE, has verified the given relative path in
    the scope at the filesystem, and has selected one authoritative snapshot.
    The caller owns cache/event invalidation after its transaction commits.
    Only an authoritative producer and increasing event position permit future
    corrections, and then only while no local write has touched that face.
    """
    _require_write_transaction(connection, scope)
    track_remote = remote_producer is not None or remote_sequence is not None
    if track_remote and (
        not isinstance(remote_producer, str) or _PRODUCER.fullmatch(remote_producer) is None
        or type(remote_sequence) is not int or not 1 <= remote_sequence < 2**63
    ):
        raise ValueError("A witnessed shared producer and event sequence are required together")
    local = _capture_verified(connection, source_path, device_id, image_id, remote, verified_hash, verified_relative_path)
    if local is None:
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
    corrections, observations = [], {}
    if track_remote:
        _create_observations(connection)
        corrections, observations = _remote_corrections(
            connection, plan=plan, rows=rows, scope=scope, producer=remote_producer,
            sequence=remote_sequence, image_id=image_id, relative=verified_relative_path,
        )
    people = _person_options(connection, scope, (*plan.carry_over, *corrections), has_links=True)
    clusters = {}
    emptied_clusters = set()
    touched_people = set()
    applied = 0
    changed_faces = False
    for match in (*candidates, *corrections):
        decision = match.decision
        row = rows[match.local.face_id]
        correction = match.local.face_id in observations
        if row["person_id"] is not None:
            touched_people.add(row["person_id"])
        if decision.kind != "unassigned":
            person_id, status = None, decision.archive_status
            if decision.kind == "person":
                if decision.person_key not in people:
                    continue
                person_id = _linked_person(connection, scope, decision, people)
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
        elif correction:
            connection.execute("UPDATE face SET cluster_id = NULL, review_status = ? WHERE id = ?",
                               (FACE_REVIEW_STATUS_ACTIVE, row["id"]))
            if row["cluster_id"] is not None:
                emptied_clusters.add(row["cluster_id"])
            changed_faces = True
        if not correction:
            connection.execute(
                "INSERT INTO shared_sync_face_receipt VALUES (?, ?, ?)", (row["id"], scope, match.shared.face_id)
            )
        if track_remote:
            observations[match.local.face_id] = match.shared
        applied += 1

    if changed_faces:
        connection.executemany(
            "DELETE FROM cluster WHERE id = ? AND NOT EXISTS (SELECT 1 FROM face WHERE cluster_id = cluster.id)",
            [(cluster_id,) for cluster_id in emptied_clusters],
        )
        mark_persons_dirty_for_recluster(connection, touched_people)
    if observations:
        fresh = capture_image(connection, source_path, device_id, image_id)
        current = scoped_snapshot(connection, scope=scope, device_id=device_id, local=fresh)
        _save_observations(
            connection, observations=observations, local=current, rows=rows, scope=scope, producer=remote_producer,
            sequence=remote_sequence, relative=verified_relative_path,
        )
    return applied


def resolve_face_conflict(
    connection, *, scope: str, source_path: str, device_id: str, image_id: int,
    remote: dict, verified_hash: str, verified_relative_path: str,
    local_face_id: str, shared_face_id: str, expected_local: Assignment | None,
    expected_shared: Assignment | None, choice: Literal["local", "shared"], resolution_event_id: str | None = None,
) -> bool:
    """Apply the reviewed side of one exact conflict in the caller's write transaction.

    The caller supplies the verified path and current peer event itself, along
    with the opaque face IDs and both decisions from the reviewed conflict.
    This function leaves latest-event, device and outbox checks to the caller.
    """
    _require_write_transaction(connection, scope)
    if resolution_event_id is not None:
        _validate_resolution_id(resolution_event_id)
    if choice not in ("local", "shared"):
        raise ValueError("Invalid shared conflict choice")
    local = _capture_verified(connection, source_path, device_id, image_id, remote, verified_hash, verified_relative_path)
    if local is None:
        raise SharedConflictChanged("The photo can no longer be verified in this shared scope")
    plan = compare_for_scope(
        connection, scope=scope, device_id=device_id, local=local, remote=remote, verified_hash=verified_hash,
    )
    pair = next((
        (issue.local[0], issue.shared[0]) for issue in plan.conflicts
        if issue.reason in ("different_decisions", "unverified_person_identity")
        and len(issue.local) == len(issue.shared) == 1
        and issue.local[0].face_id == local_face_id and issue.shared[0].face_id == shared_face_id
    ), None)
    if pair is None or pair[0].decision != expected_local or pair[1].decision != expected_shared:
        raise SharedConflictChanged("The decisions under review have changed")
    left, right = pair
    chosen = expected_shared if choice == "shared" else expected_local
    if chosen is None and choice == "shared":
        raise SharedConflictBlocked("The shared side has no confirmed decision")

    if choice == "shared" and chosen.kind == "person":
        people = _person_options(connection, scope, [FaceMatch(left, right, chosen, "shared")], has_links=_has_links(connection))
        if chosen.person_key not in people:
            raise SharedConflictBlocked("The shared person cannot be safely linked to a local person")

    _create_tables(connection)
    row = _scoped_rows(connection, scope=scope, device_id=device_id, image_id=image_id)[local_face_id]
    if _has_observations(connection):
        connection.execute("DELETE FROM shared_sync_auto_observation WHERE face_id = ?", (row["id"],))
    if choice == "shared":
        person_id = _linked_person(connection, scope, chosen, people) if chosen.kind == "person" else None
        _set_conflict_assignment(connection, row, chosen, person_id)

    _save_conflict_confirmation(
        connection, scope, source_path, device_id, image_id, row, local_face_id, right, choice, resolution_event_id,
    )
    return True


def _set_conflict_assignment(connection, row, decision: Assignment, person_id: int | None) -> None:
    status = decision.archive_status if decision.kind == "archived" else FACE_REVIEW_STATUS_ACTIVE
    cluster_id = None
    if decision.kind != "unassigned":
        cluster_id = connection.execute("INSERT INTO cluster(person_id) VALUES (?)", (person_id,)).lastrowid
    connection.execute("UPDATE face SET cluster_id = ?, review_status = ? WHERE id = ?", (cluster_id, status, row["id"]))
    if row["cluster_id"] is not None and row["label"] is None:
        connection.execute(
            "DELETE FROM cluster WHERE id = ? AND NOT EXISTS (SELECT 1 FROM face WHERE cluster_id = cluster.id)",
            (row["cluster_id"],),
        )
    mark_persons_dirty_for_recluster(connection, (row["person_id"], person_id))


def _validate_resolution_id(event_id: str) -> None:
    if not isinstance(event_id, str) or not _RESOLUTION_ID.fullmatch(event_id):
        raise ValueError("Invalid confirmed conflict identity")


def _create_confirmed_heads(connection) -> None:
    connection.execute("""
        CREATE TABLE IF NOT EXISTS shared_sync_confirmed_head (
            face_id INTEGER PRIMARY KEY REFERENCES face(id) ON DELETE CASCADE,
            scope TEXT NOT NULL,
            event_id TEXT NOT NULL,
            local_state TEXT NOT NULL
        )
    """)
    _create_invalidation_triggers(connection, "confirmed_head", "shared_sync_confirmed_head")


def confirmed_conflict_head(
    connection, *, scope: str, image_id: int, device_id: str, face: FaceEvidence,
) -> str | None:
    """Identify an exact, still-untouched confirmed write on this local face."""
    if connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'shared_sync_confirmed_head'"
    ).fetchone() is None:
        return None
    namespace = snapshot_device_namespace(device_id)
    for face_id, event_id, state in connection.execute("""
        SELECT h.face_id, h.event_id, h.local_state FROM shared_sync_confirmed_head h
        JOIN face f ON f.id = h.face_id WHERE f.image_id = ? AND h.scope = ?
    """, (image_id, scope)):
        if snapshot_face_id(namespace, face_id) == face.face_id and state == _face_state(face):
            return event_id
    return None


def _competing_person_options(connection, scope, device_id, face, decision: Assignment) -> dict[str, int | None]:
    namespace = snapshot_device_namespace(device_id)
    candidate = connection.execute(
        "SELECT id FROM person WHERE person_name_key(name) = ?", (normalize_person_name_key(decision.person_name),),
    ).fetchone()
    if candidate and str(uuid5(namespace, f"person/{candidate[0]}")) == decision.person_key:
        return {decision.person_key: candidate[0]}
    return _person_options(
        connection, scope, [FaceMatch(face, face, decision, "shared")], has_links=_has_links(connection),
    )


def is_competing_assignment_available(
    connection, *, scope: str, device_id: str, face: FaceEvidence, decision: Assignment,
) -> bool:
    """Check the exact selected identity before enabling or publishing a choice."""
    if decision.kind != "person" or (
        face.decision is not None and replace(face.decision, provenance=decision.provenance) == decision
    ):
        return True
    return decision.person_key in _competing_person_options(connection, scope, device_id, face, decision)


def _save_conflict_confirmation(
    connection, scope, source_path, device_id, image_id, row, local_face_id, right, choice, event_id,
) -> None:
    # Keeping a previously undecided face is now an explicit local unassignment.
    connection.execute(
        "INSERT OR IGNORE INTO shared_sync_face_receipt VALUES (?, ?, ?)", (row["id"], scope, right.face_id),
    )
    fresh = capture_image(connection, source_path, device_id, image_id)
    mapped = scoped_snapshot(connection, scope=scope, device_id=device_id, local=fresh)
    retained = next(face for face in mapped.faces if face.face_id == local_face_id)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS shared_sync_face_resolution (
            face_id INTEGER NOT NULL REFERENCES face(id) ON DELETE CASCADE,
            scope TEXT NOT NULL,
            shared_face_key TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            choice TEXT NOT NULL CHECK(choice IN ('local', 'shared')),
            PRIMARY KEY (face_id, scope, shared_face_key)
        )
    """)
    connection.execute("""
        INSERT INTO shared_sync_face_resolution VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(face_id, scope, shared_face_key) DO UPDATE SET fingerprint = excluded.fingerprint, choice = excluded.choice
    """, (row["id"], scope, right.face_id, _resolution_fingerprint(retained, right), choice))
    if event_id is not None:
        _create_confirmed_heads(connection)
        connection.execute("""
            INSERT INTO shared_sync_confirmed_head VALUES (?, ?, ?, ?)
            ON CONFLICT(face_id) DO UPDATE SET scope = excluded.scope, event_id = excluded.event_id,
                local_state = excluded.local_state
        """, (row["id"], scope, event_id, _face_state(retained)))


def resolve_competing_face_conflict(
    connection, *, scope: str, source_path: str, device_id: str, image_id: int,
    original_remote: dict, verified_hash: str, verified_relative_path: str, local_face_id: str, shared_face_id: str,
    expected_local: Assignment | None, expected_head: str | None, chosen: Assignment, resolution_event_id: str,
) -> bool:
    """Conclude two witnessed choices without replacing a newer local edit.

    The caller checked both competing parents and supplied the just-proven
    current local assignment and confirmed head under the same transaction.
    """
    _require_write_transaction(connection, scope)
    _validate_resolution_id(resolution_event_id)
    local = _capture_verified(
        connection, source_path, device_id, image_id, original_remote, verified_hash, verified_relative_path,
    )
    if local is None:
        raise SharedConflictChanged("The photo can no longer be verified in this shared scope")
    _create_tables(connection)
    rows = _scoped_rows(connection, scope=scope, device_id=device_id, image_id=image_id)
    local = _mapped_snapshot(local, rows)
    plan = compare_verified_photo(local, original_remote, verified_hash=verified_hash)
    pair = next((
        (left, right) for left, right, _ in _matched_pairs(plan)
        if left.face_id == local_face_id and right.face_id == shared_face_id
    ), None)
    if pair is None or pair[0].decision != expected_local:
        raise SharedConflictChanged("The confirmed face or decision has changed")
    left, right = pair
    if confirmed_conflict_head(connection, scope=scope, image_id=image_id, device_id=device_id, face=left) != expected_head:
        raise SharedConflictChanged("The local conflict was edited after its first confirmation")

    row = rows[local_face_id]
    same_decision = left.decision is not None and replace(left.decision, provenance=chosen.provenance) == chosen
    if not same_decision:
        person_id = None
        if chosen.kind == "person":
            people = _competing_person_options(connection, scope, device_id, left, chosen)
            person_id = _linked_person(connection, scope, chosen, people)
        _set_conflict_assignment(connection, row, chosen, person_id)
    if _has_observations(connection):
        connection.execute("DELETE FROM shared_sync_auto_observation WHERE face_id = ?", (row["id"],))
    _save_conflict_confirmation(
        connection, scope, source_path, device_id, image_id, row, local_face_id, right,
        "local" if same_decision else "shared", resolution_event_id,
    )
    return True
