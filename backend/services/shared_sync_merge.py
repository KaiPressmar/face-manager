"""Plan a conservative bridge from independent libraries to shared faces.

Callers supply validated evidence for one authorized share scope. Planning
does not read sources, change assignments, or publish events.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal


Box = tuple[float, float, float, float]
AssignmentKind = Literal["person", "unassigned", "archived"]
ArchiveStatus = Literal["unknown_person", "not_face"]
DecisionSource = Literal["local", "shared", "both", "none"]
ConflictReason = Literal[
    "different_decisions", "unverified_person_identity", "ambiguous_geometry", "photo_match_limit",
]

MAX_FACES_PER_PHOTO = 128
_PHOTO_HASH = re.compile(r"[0-9a-fA-F]{64}")
_MIN_MATCH_IOU = 0.80
_MIN_SCORE_GAP = 0.10
_MIN_REVIEW_IOU = 0.55
_MIN_SIDE_RATIO = 0.80
_MAX_CENTER_SHIFT = 0.15


@dataclass(frozen=True, slots=True)
class Assignment:
    """A confirmed decision. A display name by itself is not a person identity."""

    kind: AssignmentKind
    person_key: str | None = None
    person_name: str | None = None
    archive_status: ArchiveStatus | None = None
    provenance: str | None = None

    def __post_init__(self) -> None:
        if any(value is not None and (not isinstance(value, str) or not value.strip())
               for value in (self.person_key, self.person_name)):
            raise ValueError("Person keys and names must be nonempty")
        if self.kind == "person":
            if self.archive_status is not None or not (self.person_key or self.person_name):
                raise ValueError("A person decision requires a person key or legacy display name")
        elif self.kind == "archived":
            if self.archive_status not in ("unknown_person", "not_face"):
                raise ValueError("An archived decision requires a recognized archive status")
        elif self.kind != "unassigned" or self.archive_status is not None:
            raise ValueError("Invalid face decision")
        if self.kind != "person" and (self.person_key is not None or self.person_name is not None):
            raise ValueError("Only a person decision can identify a person")


@dataclass(frozen=True, slots=True)
class FaceEvidence:
    """One detected region in normalized raw-image coordinates before EXIF rotation."""

    face_id: str
    photo_sha256: str | None
    bbox: Box | None
    decision: Assignment | None = None
    photo_verified: bool = False
    geometry_reliable: bool = False


@dataclass(frozen=True, slots=True)
class FaceMatch:
    local: FaceEvidence
    shared: FaceEvidence
    decision: Assignment | None
    decision_source: DecisionSource


@dataclass(frozen=True, slots=True)
class MergeConflict:
    reason: ConflictReason
    photo_sha256: str
    local: tuple[FaceEvidence, ...]
    shared: tuple[FaceEvidence, ...]


@dataclass(frozen=True, slots=True)
class MergePlan:
    # A carry-over says which side supplies a decision. It does not resolve a
    # legacy display name to an existing person on the receiving side.
    carry_over: tuple[FaceMatch, ...]
    equivalent: tuple[FaceMatch, ...]
    conflicts: tuple[MergeConflict, ...]
    unmatched_local: tuple[FaceEvidence, ...]
    unmatched_shared: tuple[FaceEvidence, ...]


def _valid_box(bbox: Box | None) -> bool:
    if not isinstance(bbox, tuple) or len(bbox) != 4:
        return False
    if any(type(value) not in (int, float) or not 0 <= value <= 1 or not math.isfinite(value) for value in bbox):
        return False
    left, top, right, bottom = bbox
    return left < right and top < bottom


def _group_faces(
    faces: Iterable[FaceEvidence],
) -> tuple[dict[str, list[FaceEvidence]], list[FaceEvidence]]:
    faces = tuple(faces)
    counts = Counter(item.face_id for item in faces if isinstance(item.face_id, str))
    by_photo = defaultdict(list)
    unmatched = []
    for item in faces:
        photo_hash = item.photo_sha256
        if (
            not isinstance(item.face_id, str) or not item.face_id or counts[item.face_id] != 1
            or item.photo_verified is not True or item.geometry_reliable is not True
            or not isinstance(photo_hash, str) or not _PHOTO_HASH.fullmatch(photo_hash)
            or not _valid_box(item.bbox)
        ):
            unmatched.append(item)
            continue
        by_photo[photo_hash.lower()].append(item)
    for group in by_photo.values():
        group.sort(key=lambda item: item.face_id)
    return by_photo, unmatched


def _intersection_over_union(left: Box, right: Box) -> float:
    x1, y1, x2, y2 = left
    u1, v1, u2, v2 = right
    intersection = max(0.0, min(x2, u2) - max(x1, u1)) * max(0.0, min(y2, v2) - max(y1, v1))
    if not intersection:
        return 0.0
    union = (x2 - x1) * (y2 - y1) + (u2 - u1) * (v2 - v1) - intersection
    return intersection / union


def _similar_centers_and_sizes(left: Box, right: Box) -> bool:
    for start, end in ((0, 2), (1, 3)):
        small, large = sorted((left[end] - left[start], right[end] - right[start]))
        center_shift = abs((left[start] + left[end]) - (right[start] + right[end])) / 2
        if small < large * _MIN_SIDE_RATIO or center_shift > small * _MAX_CENTER_SHIFT:
            return False
    return True


def _distinct_best(scores: Iterable[float]) -> int | None:
    ranked = sorted(enumerate(scores), key=lambda candidate: candidate[1], reverse=True)
    best_index, best_score = ranked[0]
    if best_score < _MIN_MATCH_IOU:
        return None
    if len(ranked) > 1 and best_score - ranked[1][1] < _MIN_SCORE_GAP:
        return None
    return best_index


def _decision_relation(local: Assignment, shared: Assignment) -> ConflictReason | None:
    if local.kind != shared.kind:
        return "different_decisions"
    if local.kind == "person":
        if not local.person_key or not shared.person_key:
            return "unverified_person_identity"
        if local.person_key != shared.person_key:
            return "different_decisions"
    if local.kind == "archived" and local.archive_status != shared.archive_status:
        return "different_decisions"
    return None


def plan_merge(local_faces: Iterable[FaceEvidence], shared_faces: Iterable[FaceEvidence]) -> MergePlan:
    """Link reciprocal, distinct geometric matches within identical verified photos.

    Fresh lack of a decision never replaces an earlier decision. Explicitly
    undoing an assignment is a decision, so it can conflict with a name.
    Unmatched evidence, including a face a newer detector missed, stays visible
    for later resolution.
    """
    local_by_photo, unmatched_local = _group_faces(local_faces)
    shared_by_photo, unmatched_shared = _group_faces(shared_faces)
    carry_over = []
    equivalent = []
    conflicts = []

    for photo_hash in sorted(local_by_photo.keys() | shared_by_photo.keys()):
        local = local_by_photo.get(photo_hash, [])
        shared = shared_by_photo.get(photo_hash, [])
        if not local or not shared:
            unmatched_local.extend(local)
            unmatched_shared.extend(shared)
            continue
        if max(len(local), len(shared)) > MAX_FACES_PER_PHOTO:
            conflicts.append(MergeConflict("photo_match_limit", photo_hash, tuple(local), tuple(shared)))
            continue

        scores = [[_intersection_over_union(first.bbox, second.bbox) for second in shared] for first in local]
        local_best = [_distinct_best(row) for row in scores]
        shared_best = [_distinct_best(column) for column in zip(*scores)]
        matched_local = set()
        matched_shared = set()
        for i, j in enumerate(local_best):
            if j is None or shared_best[j] != i or not _similar_centers_and_sizes(local[i].bbox, shared[j].bbox):
                continue
            first, second = local[i], shared[j]
            matched_local.add(i)
            matched_shared.add(j)
            if first.decision is None and second.decision is None:
                equivalent.append(FaceMatch(first, second, None, "none"))
            elif first.decision is None:
                carry_over.append(FaceMatch(first, second, second.decision, "shared"))
            elif second.decision is None:
                carry_over.append(FaceMatch(first, second, first.decision, "local"))
            else:
                reason = _decision_relation(first.decision, second.decision)
                if reason is None:
                    equivalent.append(FaceMatch(first, second, second.decision, "both"))
                else:
                    conflicts.append(MergeConflict(reason, photo_hash, (first,), (second,)))

        possible_local = set()
        possible_shared = set()
        for i, row in enumerate(scores):
            if i in matched_local:
                continue
            for j, score in enumerate(row):
                if j not in matched_shared and score >= _MIN_REVIEW_IOU:
                    possible_local.add(i)
                    possible_shared.add(j)
        if possible_local:
            conflicts.append(MergeConflict(
                "ambiguous_geometry", photo_hash,
                tuple(local[i] for i in sorted(possible_local)),
                tuple(shared[j] for j in sorted(possible_shared)),
            ))
        unmatched_local.extend(item for i, item in enumerate(local) if i not in matched_local | possible_local)
        unmatched_shared.extend(item for j, item in enumerate(shared) if j not in matched_shared | possible_shared)

    def evidence_order(item: FaceEvidence) -> tuple[str, str]:
        return (
            item.photo_sha256 if isinstance(item.photo_sha256, str) else "",
            item.face_id if isinstance(item.face_id, str) else "",
        )

    return MergePlan(
        tuple(carry_over), tuple(equivalent), tuple(conflicts),
        tuple(sorted(unmatched_local, key=evidence_order)),
        tuple(sorted(unmatched_shared, key=evidence_order)),
    )
