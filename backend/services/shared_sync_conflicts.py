"""Review and transport exact, witnessed conflicts between two pinned authors."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, replace

from .shared_sync_journal import ExchangeJournal, PublishedEvent, digest
from .shared_sync_merge import Assignment, FaceEvidence, MergePlan
from .shared_sync_outbox import DirtyImage, revisions_match
from .shared_sync_reconcile import _assignment, _canonical_uuid, compare_verified_photo
from .shared_sync_resolution import (
    BranchDecision, ConflictResolution, ResolutionStatus, assess_resolution, decode_resolution,
    encode_resolution, mark_resolution_applied, record_resolution, shared_library_scope,
)
from .shared_sync_snapshot import SnapshotPhoto
from .shared_sync_transport import MAX_EVENTS_PER_BATCH
from .shared_sync_worker import portable_relative_path


_PRODUCER = re.compile(r"[a-f0-9]{32}")
_PHOTO = re.compile(r"[a-f0-9]{64}")
_EXACT_CONFLICTS = {"different_decisions", "unverified_person_identity"}
_MAX_BATCH = 2**63 - 1


class ConflictEvidencePending(ValueError):
    """A received resolution arrived before one of its witnessed branches."""


@dataclass(frozen=True, slots=True)
class BranchProof:
    producer: str
    batch: int
    position: int
    face_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.producer, str) or not _PRODUCER.fullmatch(self.producer):
            raise ValueError("Invalid shared decision author")
        if type(self.batch) is not int or not 1 <= self.batch <= _MAX_BATCH:
            raise ValueError("Invalid shared decision batch")
        if type(self.position) is not int or not 0 <= self.position < MAX_EVENTS_PER_BATCH:
            raise ValueError("Invalid shared decision position")
        _canonical_uuid(self.face_id)


@dataclass(frozen=True, slots=True)
class ConflictCase:
    library_scope: str
    photo_sha256: str
    relative_path: str
    subject_id: str
    local_proof: BranchProof
    peer_proof: BranchProof
    local_branch: BranchDecision
    peer_branch: BranchDecision

    @property
    def revision(self) -> str:
        return digest([
            "photo-conflict-v1", self.library_scope, self.photo_sha256, self.subject_id, self.relative_path,
            sorted((self.local_branch.revision, self.peer_branch.revision)),
        ])

    def packet(self, selected_revision: str) -> dict:
        resolution = ConflictResolution(
            self.library_scope, self.photo_sha256, self.subject_id,
            (self.local_branch, self.peer_branch), selected_revision,
        )
        return {
            "type": "conflict_resolution_proof",
            "version": 1,
            "photo_sha256": self.photo_sha256,
            "relative_paths": [self.relative_path],
            "resolution": encode_resolution(resolution),
            "proofs": [
                asdict(proof) for proof in sorted((self.local_proof, self.peer_proof), key=lambda item: item.producer)
            ],
        }


@dataclass(frozen=True, slots=True)
class VerifiedConflict:
    case: ConflictCase
    resolution: ConflictResolution
    local_event: PublishedEvent
    peer_event: PublishedEvent


def _pair(
    local: SnapshotPhoto, peer: dict, local_face_id: str, peer_face_id: str, verified_hash: str,
) -> tuple[FaceEvidence, FaceEvidence] | None:
    return _pair_from_plan(compare_verified_photo(local, peer, verified_hash=verified_hash), local_face_id, peer_face_id)


def _pair_from_plan(plan: MergePlan, local_face_id: str, peer_face_id: str) -> tuple[FaceEvidence, FaceEvidence] | None:
    for conflict in plan.conflicts:
        if conflict.reason in _EXACT_CONFLICTS and len(conflict.local) == len(conflict.shared) == 1:
            left, right = conflict.local[0], conflict.shared[0]
            if left.face_id == local_face_id and right.face_id == peer_face_id:
                return left, right
    return None


def _same_decision(left: Assignment | None, right: Assignment) -> bool:
    return left is not None and replace(left, provenance=right.provenance) == right


def _face_at(photo: SnapshotPhoto, face_id: str) -> FaceEvidence:
    return next(face for face in photo.faces if face.face_id == face_id)


def _proof_snapshot(entry: PublishedEvent, photo_sha256: str, relative: str) -> SnapshotPhoto:
    event = entry.event
    paths = event.get("relative_paths")
    raw_faces = event.get("faces")
    if (
        event.get("type") != "photo_snapshot" or type(event.get("version")) is not int or event["version"] != 1
        or event.get("photo_sha256") != photo_sha256 or not isinstance(paths, list) or not 1 <= len(paths) <= 256
        or relative not in {"/".join(portable_relative_path(path)) for path in paths}
        or not isinstance(raw_faces, list) or not 1 <= len(raw_faces) <= 512
    ):
        raise ValueError("Shared conflict proof does not describe the verified photo")
    geometry = (event.get("raw_width"), event.get("raw_height"), event.get("exif_orientation"))
    width, height, orientation = geometry
    if type(width) is not int or type(height) is not int or type(orientation) is not int:
        raise ValueError("Shared conflict proof has no verified geometry")
    if width < 1 or height < 1 or not 1 <= orientation <= 8:
        raise ValueError("Shared conflict proof has invalid geometry")
    faces = []
    for face in raw_faces:
        if not isinstance(face, dict) or set(face) != {"face_id", "bbox", "decision"}:
            raise ValueError("Invalid shared conflict face proof")
        box = face["bbox"]
        faces.append(FaceEvidence(
            _canonical_uuid(face["face_id"]), photo_sha256, tuple(box) if isinstance(box, list) else None,
            _assignment(face["decision"]), photo_verified=True, geometry_reliable=True,
        ))
    return SnapshotPhoto(0, photo_sha256, (relative,), tuple(faces), *geometry)


def _make_case(
    manifest: dict, photo_sha256: str, relative_path: str,
    local_entry: PublishedEvent, peer_entry: PublishedEvent, local_face_id: str, peer_face_id: str,
) -> ConflictCase:
    library = shared_library_scope(manifest)
    if local_entry.producer == peer_entry.producer:
        raise ValueError("A conflict requires two distinct authors")
    left_photo = _proof_snapshot(local_entry, photo_sha256, relative_path)
    right_photo = _proof_snapshot(peer_entry, photo_sha256, relative_path)
    pair = _pair(left_photo, peer_entry.event, local_face_id, peer_face_id, photo_sha256)
    if pair is None or not all(face.decision is not None for face in pair):
        raise ValueError("Published evidence does not describe a unique conflicting decision")
    if (left_photo.raw_width, left_photo.raw_height, left_photo.exif_orientation) != (
        right_photo.raw_width, right_photo.raw_height, right_photo.exif_orientation
    ):
        raise ValueError("Shared conflict proofs disagree about photo geometry")
    left = BranchProof(local_entry.producer, local_entry.batch, local_entry.position, local_face_id)
    right = BranchProof(peer_entry.producer, peer_entry.batch, peer_entry.position, peer_face_id)
    subject = digest(["photo-conflict-face-v1", photo_sha256, sorted((local_face_id, peer_face_id))])

    def branch(proof: BranchProof, published: PublishedEvent, face: FaceEvidence) -> BranchDecision:
        revision = digest(["witnessed-photo-face-decision-v1", library, photo_sha256, asdict(proof), digest(published.event)])
        return BranchDecision(revision, face.decision)

    return ConflictCase(
        library, photo_sha256, relative_path, subject, left, right,
        branch(left, local_entry, pair[0]), branch(right, peer_entry, pair[1]),
    )


def build_conflict_case(
    journal: ExchangeJournal, local_scope: str, manifest: dict, local_producer: str, peer_producer: str,
    local: SnapshotPhoto, local_face_id: str, peer_face_id: str, *, verified_hash: str, verified_relative_path: str,
) -> ConflictCase | None:
    """Offer a choice only when the current pair equals both published decisions."""
    if (
        verified_hash != local.photo_sha256 or verified_relative_path not in local.relative_paths
        or journal.bound_manifest(local_scope) != manifest
    ):
        return None
    own = journal.latest_published_photo(local_scope, local_producer, verified_hash)
    peer = journal.latest_published_photo(local_scope, peer_producer, verified_hash)
    if own is None or peer is None:
        return None
    try:
        case = _make_case(manifest, verified_hash, verified_relative_path, own, peer, local_face_id, peer_face_id)
        current = _pair(local, peer.event, local_face_id, peer_face_id, verified_hash)
        if current is None or current[0].decision != case.local_branch.decision or current[1].decision != case.peer_branch.decision:
            return None
        original = _proof_snapshot(own, verified_hash, verified_relative_path)
        original_face = _face_at(original, local_face_id)
        return case if current[0].bbox == original_face.bbox else None
    except ValueError:
        return None


def validate_conflict_packet(
    journal: ExchangeJournal, local_scope: str, manifest: dict, local_producer: str, peer_producer: str,
    author: str, packet: dict,
) -> VerifiedConflict:
    """Check the authenticated sender and both actual historical branch proofs."""
    if journal.bound_manifest(local_scope) != manifest:
        raise ValueError("The resolution does not use this witnessed share identity")
    if author not in (local_producer, peer_producer):
        raise ValueError("The resolution author is outside this two-device trial")
    if not isinstance(packet, dict) or set(packet) != {
        "type", "version", "photo_sha256", "relative_paths", "resolution", "proofs",
    }:
        raise ValueError("Invalid shared conflict packet")
    if packet["type"] != "conflict_resolution_proof" or type(packet["version"]) is not int or packet["version"] != 1:
        raise ValueError("Unsupported shared conflict packet")
    photo_sha256, paths, values = packet["photo_sha256"], packet["relative_paths"], packet["proofs"]
    if not isinstance(photo_sha256, str) or not _PHOTO.fullmatch(photo_sha256):
        raise ValueError("Invalid shared conflict photo")
    if not isinstance(paths, list) or len(paths) != 1 or not isinstance(values, list) or len(values) != 2:
        raise ValueError("Invalid shared conflict proof count")
    relative = "/".join(portable_relative_path(paths[0]))
    proofs = {}
    for value in values:
        if not isinstance(value, dict) or set(value) != {"producer", "batch", "position", "face_id"}:
            raise ValueError("Invalid shared conflict proof")
        proof = BranchProof(**value)
        proofs[proof.producer] = proof
    if set(proofs) != {local_producer, peer_producer} or len(proofs) != 2:
        raise ValueError("The resolution does not prove both pinned authors")
    own, peer = proofs[local_producer], proofs[peer_producer]
    own_event = journal.published_event(local_scope, own.producer, own.batch, own.position)
    peer_event = journal.published_event(local_scope, peer.producer, peer.batch, peer.position)
    if own_event is None or peer_event is None:
        raise ConflictEvidencePending("The resolution references evidence that has not been received")
    case = _make_case(manifest, photo_sha256, relative, own_event, peer_event, own.face_id, peer.face_id)
    resolution = decode_resolution(packet["resolution"])
    if resolution != decode_resolution(case.packet(resolution.selected_revision)["resolution"]):
        raise ValueError("The shared conflict resolution does not match the witnessed decisions")
    return VerifiedConflict(case, resolution, own_event, peer_event)


def verified_current_parents(
    journal: ExchangeJournal, local_scope: str, witnessed: VerifiedConflict, local: SnapshotPhoto,
) -> tuple[BranchDecision, ...]:
    """Return exact current branches, including a peer's matching re-publication.

    A later peer snapshot may repeat the selected decision by the time this
    device receives the resolution. The local branch and both face geometries
    must still be the originally witnessed ones.
    """
    case = witnessed.case
    photo_hash, relative = case.photo_sha256, case.relative_path
    if local.photo_sha256 != photo_hash or relative not in local.relative_paths:
        return ()
    own_latest = journal.latest_published_photo(local_scope, case.local_proof.producer, photo_hash)
    peer_latest = journal.latest_published_photo(local_scope, case.peer_proof.producer, photo_hash)
    if own_latest != witnessed.local_event or peer_latest is None:
        return ()

    own_face, peer_face = case.local_proof.face_id, case.peer_proof.face_id
    try:
        original_pair = _pair(local, witnessed.peer_event.event, own_face, peer_face, photo_hash)
        if original_pair is None or original_pair[0].decision != case.local_branch.decision:
            return ()
        original = _proof_snapshot(witnessed.local_event, photo_hash, relative)
        if original_pair[0].bbox != _face_at(original, own_face).bbox:
            return ()
        parents = (case.local_branch, case.peer_branch)
        if peer_latest == witnessed.peer_event:
            return parents
        if (peer_latest.batch, peer_latest.position) <= (witnessed.peer_event.batch, witnessed.peer_event.position):
            return ()
        latest = _proof_snapshot(peer_latest, photo_hash, relative)
        candidates = [face for face in latest.faces if face.face_id == peer_face]
        if len(candidates) != 1:
            return ()
        peer = candidates[0]
        if peer.bbox != original_pair[1].bbox or not _same_decision(peer.decision, witnessed.resolution.chosen_decision):
            return ()
        plan = compare_verified_photo(local, peer_latest.event, verified_hash=photo_hash)
        exact_match = any(
            match.local.face_id == own_face and match.shared.face_id == peer_face
            for match in plan.equivalent
        ) or _pair_from_plan(plan, own_face, peer_face) is not None
        return parents if exact_match else ()
    except ValueError:
        return ()


def reserve_conflict_guard(conn: sqlite3.Connection, outbox_scope: str, image_id: int) -> DirtyImage:
    """Reserve a fresh local outbox revision in the caller's write transaction."""
    if not conn.in_transaction or type(image_id) is not int or image_id <= 0:
        raise ValueError("A local image and caller-owned write transaction are required")
    updated = conn.execute(
        "UPDATE shared_sync_outbox_scope SET revision = revision + 1 WHERE id = ?", (outbox_scope,),
    )
    if updated.rowcount != 1:
        raise ValueError("The image scope is not registered for sync")
    revision, = conn.execute(
        "SELECT revision FROM shared_sync_outbox_scope WHERE id = ?", (outbox_scope,),
    ).fetchone()
    conn.execute("""
        INSERT INTO shared_sync_dirty_image (scope_id, image_id, revision) VALUES (?, ?, ?)
        ON CONFLICT (scope_id, image_id) DO UPDATE SET revision = excluded.revision
    """, (outbox_scope, image_id, revision))
    return DirtyImage(image_id, revision)


def stage_conflict_choice(
    conn: sqlite3.Connection, journal: ExchangeJournal, local_scope: str, outbox_scope: str,
    case: ConflictCase, expected_revision: str, selected_revision: str, guard: DirtyImage,
) -> None:
    """Queue the explicit choice, without changing the live photo or its normal outbox receipt.

    The caller rebuilt the case from current evidence under the same write
    transaction and separately verified the physical source SHA and path.
    """
    if (
        case.library_scope != shared_library_scope(journal.bound_manifest(local_scope))
        or expected_revision != case.revision or not revisions_match(conn, outbox_scope, (guard,))
    ):
        raise ValueError("The reviewed conflict has changed")
    for proof in (case.local_proof, case.peer_proof):
        latest = journal.latest_published_photo(local_scope, proof.producer, case.photo_sha256)
        if latest is None or (latest.batch, latest.position) != (proof.batch, proof.position):
            raise ValueError("The reviewed shared decision has changed")
    journal.stage(
        local_scope, [case.packet(selected_revision)], local_rows=((guard.image_id, guard.revision),),
        acknowledge_rows=False,
    )


def apply_verified_choice(
    conn: sqlite3.Connection, conflict: VerifiedConflict, current_parents: Iterable[BranchDecision],
    write_choice: Callable[[Assignment, str], None],
) -> ResolutionStatus:
    """Apply a received choice only if the caller's freshly checked heads are exact.

    The caller verifies the source location and holds BEGIN IMMEDIATE.
    The callback must raise if it cannot apply the exact assignment safely.
    """
    resolution = conflict.resolution
    record_resolution(conn, resolution)
    parents = tuple(current_parents)
    status = assess_resolution(conn, resolution, parents)
    if status != "ready":
        return status
    conn.execute("SAVEPOINT shared_conflict_application")
    try:
        if not mark_resolution_applied(conn, resolution, parents):
            raise ValueError("The conflict can no longer be applied")
        write_choice(resolution.chosen_decision, resolution.event_id)
    except Exception:
        conn.execute("ROLLBACK TO shared_conflict_application")
        raise
    finally:
        conn.execute("RELEASE shared_conflict_application")
    return "applied"
