import math
import unittest
from dataclasses import replace

from backend.services.shared_sync_merge import Assignment, FaceEvidence, MAX_FACES_PER_PHOTO, plan_merge


PHOTO = "a1" * 32
OTHER = "b2" * 32
BOX = (0.10, 0.20, 0.30, 0.40)
ANNA = Assignment("person", person_key="anna", person_name="Anna")


def face(face_id, decision=None, *, bbox=BOX, digest=PHOTO):
    return FaceEvidence(face_id, digest, bbox, decision, photo_verified=True, geometry_reliable=True)


class SharedSyncMergeTest(unittest.TestCase):
    def test_old_confirmed_name_survives_fresh_detection_in_either_direction(self):
        old = face("old-1", Assignment("person", person_name="Anna", provenance="PC2"))
        fresh = face("fresh-1", bbox=(0.101, 0.201, 0.302, 0.401), digest=PHOTO.upper())
        self.assertEqual(fresh.decision, None)
        for local, shared, source in ((old, fresh, "local"), (fresh, old, "shared")):
            with self.subTest(source=source):
                plan = plan_merge([local], [shared])
                match, = plan.carry_over
                self.assertEqual((match.local, match.shared), (local, shared))
                self.assertEqual((match.decision, match.decision_source), (old.decision, source))
                self.assertEqual(plan.conflicts, ())
                self.assertEqual(plan.unmatched_local + plan.unmatched_shared, ())
        self.assertEqual(old.decision.provenance, "PC2")
        self.assertIsNone(fresh.decision)

    def test_same_name_can_be_different_people_and_same_key_can_be_renamed(self):
        for other, expected_reason in (
            (Assignment("person", person_name="Anna"), "unverified_person_identity"),
            (Assignment("person", person_key="person-on-another-pc", person_name="Anna"), "different_decisions"),
        ):
            with self.subTest(other=other):
                plan = plan_merge([face("local", ANNA)], [face("shared", other)])
                conflict, = plan.conflicts
                self.assertEqual(conflict.reason, expected_reason)
                self.assertEqual(conflict.local[0].decision, ANNA)
                self.assertEqual(conflict.shared[0].decision, other)
                self.assertFalse(plan.equivalent or plan.carry_over)
        renamed = Assignment("person", person_key="anna", person_name="Anna M.")
        plan = plan_merge([face("local", ANNA)], [face("shared", renamed)])
        self.assertEqual(plan.equivalent[0].decision_source, "both")

    def test_explicit_unassignment_conflicts_while_fresh_none_does_not(self):
        confirmed_unassigned = Assignment("unassigned")
        conflict, = plan_merge(
            [face("named", ANNA)], [face("unassigned", confirmed_unassigned)],
        ).conflicts
        self.assertEqual(conflict.reason, "different_decisions")
        carry, = plan_merge([face("fresh")], [face("unassigned", confirmed_unassigned)]).carry_over
        self.assertEqual(carry.decision, confirmed_unassigned)
        equivalent, = plan_merge([face("fresh-1")], [face("fresh-2")]).equivalent
        self.assertIsNone(equivalent.decision)
        self.assertEqual(equivalent.decision_source, "none")

    def test_archived_no_face_is_explicit_and_not_lost_if_new_detector_misses_it(self):
        rejected = face("old-not-face", Assignment("archived", archive_status="not_face"))
        plan = plan_merge([rejected], [])
        self.assertEqual(plan.unmatched_local, (rejected,))
        self.assertEqual(plan.unmatched_local[0].decision.archive_status, "not_face")
        fresh = face("fresh")
        carry, = plan_merge([rejected], [fresh]).carry_over
        self.assertEqual(carry.decision, rejected.decision)
        unknown = face("shared-unknown", Assignment("archived", archive_status="unknown_person"))
        conflict, = plan_merge([rejected], [unknown]).conflicts
        self.assertEqual(conflict.reason, "different_decisions")

    def test_photo_or_geometry_without_verification_never_carries_legacy_decisions(self):
        for uncertain in (
            replace(face("old", ANNA), photo_verified=False),
            replace(face("old", ANNA), geometry_reliable=False),
            face("old", ANNA, digest=None),
            face("old", ANNA, digest=OTHER),
            face("old", ANNA, digest="invalid"),
            face("old", ANNA, bbox=None),
            face("old", ANNA, bbox=(0, math.nan, 0.4, 0.5)),
            face("old", ANNA, bbox=(0.1, 0.1, 0.1, 0.4)),
        ):
            with self.subTest(uncertain=uncertain):
                fresh = face("fresh")
                plan = plan_merge([uncertain], [fresh])
                self.assertEqual(plan.unmatched_local, (uncertain,))
                self.assertEqual(plan.unmatched_shared, (fresh,))
                self.assertFalse(plan.carry_over or plan.equivalent)

    def test_ambiguous_geometry_keeps_all_competitors(self):
        old = face("legacy", ANNA)
        first = face("candidate-a")
        second = face("candidate-b", bbox=(0.104, 0.204, 0.304, 0.404))
        plan = plan_merge([old], [second, first])
        conflict, = plan.conflicts
        self.assertEqual(conflict.reason, "ambiguous_geometry")
        self.assertEqual(conflict.local, (old,))
        self.assertEqual(conflict.shared, (first, second))
        self.assertFalse(plan.carry_over or plan.equivalent)
        self.assertEqual(plan, plan_merge([old], [first, second]))

    def test_reciprocity_and_near_threshold_alternative_prevent_guessing(self):
        exact = face("current")
        near_threshold = face("other-current", bbox=(0.123, 0.2, 0.323, 0.4))
        drifted = face("shifted", ANNA, bbox=(0.111, 0.2, 0.311, 0.4))
        plan = plan_merge([drifted], [exact, near_threshold])
        self.assertFalse(plan.carry_over)
        self.assertEqual(plan.conflicts[0].reason, "ambiguous_geometry")
        plan = plan_merge([exact, near_threshold], [drifted])
        self.assertFalse(plan.carry_over)
        self.assertEqual(plan.conflicts[0].reason, "ambiguous_geometry")

    def test_duplicate_identity_is_held_unmatched_instead_of_applied_twice(self):
        first = face("duplicate", ANNA)
        second = face("duplicate", ANNA, digest=OTHER)
        plan = plan_merge([first, second], [face("current")])
        self.assertEqual(plan.unmatched_local, (first, second))
        self.assertFalse(plan.carry_over)

    def test_dense_photo_refuses_matching_without_truncating_evidence(self):
        crowded = tuple(face(f"legacy-{index:03d}") for index in range(MAX_FACES_PER_PHOTO + 1))
        unaffected = face("other", ANNA, digest=OTHER)
        shared = face("current")
        plan = plan_merge((*crowded, unaffected), (shared, face("other-current", digest=OTHER)))
        conflict, = plan.conflicts
        self.assertEqual(conflict.reason, "photo_match_limit")
        self.assertEqual(conflict.local, crowded)
        self.assertEqual(conflict.shared, (shared,))
        self.assertEqual(plan.carry_over[0].local, unaffected)


if __name__ == "__main__":
    unittest.main()
