from copy import deepcopy
from dataclasses import asdict
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from backend.db import schema
from backend.services.shared_sync_apply import (
    SharedConflictBlocked, SharedConflictChanged, apply_unambiguous, compare_for_scope,
    is_conflict_person_available, resolve_face_conflict, scoped_snapshot,
)
from backend.services.shared_sync_merge import Assignment, FaceEvidence
from backend.services.shared_sync_reconcile import photo_event
from backend.services.shared_sync_snapshot import SnapshotPhoto, capture_image


HASH = "a9" * 32
SOURCE = "vacation.jpg"
SCOPE = "library/current-generation"
DEVICE = "receiving-device"
REMOTE_DEVICE = "2" * 32


class SharedSyncApplyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        parent = Path(directory.name)
        self.root = parent / "shared"
        override = patch.object(schema, "DB_PATH", str(parent / "library.sqlite"))
        override.start()
        self.addCleanup(override.stop)
        schema.init_db(scan_sources=False)
        self.connection = schema.get_conn()
        self.addCleanup(self.connection.close)
        path = str(self.root / SOURCE)
        self.connection.execute(
            """INSERT INTO image(id,path,directory,filename,content_hash,processed_at,
                                 raw_width,raw_height,exif_orientation)
               VALUES(1,?,?,?,?,?,100,100,1)""",
            (path, str(self.root), SOURCE, HASH, "done"),
        )
        self._add_location(self.root / SOURCE)
        self.connection.commit()

    def _add_location(self, path):
        self.connection.execute(
            "INSERT INTO image_location(image_id,path,directory,filename) VALUES(1,?,?,?)",
            (str(path), str(path.parent), path.name),
        )

    def _face(self, face_id, *, bbox=(10, 10, 20, 20), cluster_id=None, status="active"):
        self.connection.execute(
            """INSERT INTO face(id,image_id,bbox_x,bbox_y,bbox_w,bbox_h,review_status,cluster_id)
               VALUES(?,1,?,?,?,?,?,?)""",
            (face_id, *bbox, status, cluster_id),
        )
        x, y, w, h = bbox
        return (x / 100, y / 100, (x + w) / 100, (y + h) / 100)

    def _remote(self, *pairs, paths=(SOURCE,)):
        faces = tuple(FaceEvidence(str(uuid4()), HASH, bbox, assignment, True, True) for bbox, assignment in pairs)
        return photo_event(SnapshotPhoto(1, HASH, paths, faces, 100, 100, 1), "Sender")

    def _apply(self, remote, *, scope=SCOPE, relative_path=SOURCE, verified_hash=HASH, **proof):
        return apply_unambiguous(
            self.connection, scope=scope, source_path=self.root, device_id=DEVICE, image_id=1,
            remote=remote, verified_hash=verified_hash, verified_relative_path=relative_path, **proof,
        )

    def _observe(self, remote, sequence, producer=REMOTE_DEVICE):
        return self._apply(remote, remote_producer=producer, remote_sequence=sequence)

    @staticmethod
    def _correct(remote, decision):
        change = deepcopy(remote)
        change["faces"][0]["decision"] = asdict(decision) if decision else None
        return change

    def _start(self):
        self.connection.commit()
        self.connection.execute("BEGIN IMMEDIATE")

    def _state(self):
        return [tuple(row) for row in self.connection.execute("""
            SELECT f.id, f.review_status, p.name FROM face f
            LEFT JOIN cluster c ON f.cluster_id = c.id LEFT JOIN person p ON c.person_id = p.id
            ORDER BY f.id
        """)]

    def _compare(self, remote):
        return compare_for_scope(
            self.connection, scope=SCOPE, device_id=DEVICE,
            local=capture_image(self.connection, self.root, DEVICE, 1), remote=remote, verified_hash=HASH,
        )

    def _resolve(self, remote, conflict, choice, **updates):
        left, = conflict.local
        right, = conflict.shared
        params = {
            "scope": SCOPE, "source_path": self.root, "device_id": DEVICE, "image_id": 1,
            "remote": remote, "verified_hash": HASH, "verified_relative_path": SOURCE,
            "local_face_id": left.face_id, "shared_face_id": right.face_id,
            "expected_local": left.decision, "expected_shared": right.decision, "choice": choice,
        }
        return resolve_face_conflict(self.connection, **{**params, **updates})

    def test_shared_person_and_archive_are_applied_once_with_canonical_export_identity(self):
        person_key = str(uuid4())
        anna = Assignment("person", person_key, "Anna", provenance="confirmed")
        hidden = Assignment("archived", archive_status="not_face", provenance="confirmed")
        person_box = self._face(1)
        second_box = self._face(2, bbox=(40, 10, 20, 20))
        hidden_box = self._face(3, bbox=(70, 10, 20, 20))
        remote = self._remote((person_box, anna), (second_box, anna), (hidden_box, hidden))
        self._start()

        self.assertEqual(self._apply(remote), 3)
        self.assertEqual(self._state(), [(1, "active", "Anna"), (2, "active", "Anna"), (3, "not_face", None)])
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM person").fetchone()[0], 1)
        self.assertEqual(self._apply(remote), 0)
        local = capture_image(self.connection, self.root, DEVICE, 1)
        scoped = scoped_snapshot(self.connection, scope=SCOPE, device_id=DEVICE, local=local)
        self.assertEqual(scoped.faces[0].decision.person_key, person_key)
        comparison = compare_for_scope(
            self.connection, scope=SCOPE, device_id=DEVICE, local=local, remote=remote, verified_hash=HASH,
        )
        self.assertEqual(len(comparison.equivalent), 3)
        self.assertFalse(comparison.conflicts)
        self.assertEqual([row[0] for row in self.connection.execute(
            "SELECT person_id FROM recluster_dirty_person ORDER BY person_id"
        )], [-1, 1])

    def test_caller_rollback_is_atomic_and_restores_assignments_and_links(self):
        box = self._face(1)
        remote = self._remote((box, Assignment("person", str(uuid4()), "Anna", provenance="legacy")))
        self._start()
        self.assertEqual(self._apply(remote), 1)
        self.assertTrue(self.connection.in_transaction)
        self.connection.rollback()
        self.assertEqual(self._state(), [(1, "active", None)])
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM person").fetchone()[0], 0)
        self._start()
        self.assertEqual(self._apply(remote), 1)

    def test_same_name_or_legacy_name_without_key_does_not_merge(self):
        self.connection.execute("INSERT INTO person(name) VALUES ('A\N{COMBINING DIAERESIS}NNA ')")
        first = self._face(1)
        second = self._face(2, bbox=(40, 10, 20, 20))
        remote = self._remote(
            (first, Assignment("person", str(uuid4()), "  änna", provenance="confirmed")),
            (second, Assignment("person", person_name="Bea", provenance="legacy")),
        )
        self._start()
        self.assertEqual(self._apply(remote), 0)
        self.assertEqual(self._state(), [(1, "active", None), (2, "active", None)])
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM person").fetchone()[0], 1)
        comparison = compare_for_scope(
            self.connection, scope=SCOPE, device_id=DEVICE,
            local=capture_image(self.connection, self.root, DEVICE, 1), remote=remote, verified_hash=HASH,
        )
        self.assertFalse(comparison.carry_over)
        self.assertEqual([issue.reason for issue in comparison.conflicts], ["unverified_person_identity"] * 2)

    def test_two_new_remote_identities_with_same_name_both_stay_pending(self):
        first = self._face(1)
        second = self._face(2, bbox=(40, 10, 20, 20))
        remote = self._remote(
            (first, Assignment("person", str(uuid4()), "Anna", provenance="confirmed")),
            (second, Assignment("person", str(uuid4()), "anna", provenance="confirmed")),
        )
        self._start()
        self.assertEqual(self._apply(remote), 0)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM person").fetchone()[0], 0)

    def test_receipts_preserve_undo_and_person_deletion_across_replay(self):
        anna = Assignment("person", str(uuid4()), "Anna", provenance="confirmed")
        first = self._face(1)
        second = self._face(2, bbox=(40, 10, 20, 20))
        remote = self._remote((first, anna))
        self._start()
        self.assertEqual(self._apply(remote), 1)
        self.connection.execute("UPDATE face SET cluster_id = NULL WHERE id = 1")
        self.connection.execute("DELETE FROM cluster WHERE person_id IS NOT NULL")
        self.connection.execute("DELETE FROM person")
        self.assertEqual(self._apply(remote), 0)
        self.assertEqual(self._apply(self._remote((second, anna))), 0)
        self.assertEqual(self._state(), [(1, "active", None), (2, "active", None)])
        self.assertIsNone(self.connection.execute("SELECT person_id FROM shared_sync_person_link").fetchone()[0])

    def test_person_links_are_scoped_and_keep_a_local_rename(self):
        anna = Assignment("person", str(uuid4()), "Anna", provenance="confirmed")
        first = self._face(1)
        second = self._face(2, bbox=(40, 10, 20, 20))
        self._start()
        self.assertEqual(self._apply(self._remote((first, anna))), 1)
        remote = self._remote((second, anna))
        self.assertEqual(self._apply(remote, scope="other-generation"), 0)
        self.connection.execute("UPDATE person SET name = 'Anna (lokal)'")
        self.assertEqual(self._apply(remote), 1)
        self.assertEqual(self._state(), [(1, "active", "Anna (lokal)"), (2, "active", "Anna (lokal)")])

    def test_existing_decisions_and_ambiguous_geometry_remain_unchanged(self):
        self.connection.execute("INSERT INTO person(id,name) VALUES (1,'Local')")
        self.connection.execute("INSERT INTO cluster(id,person_id) VALUES (1,1)")
        named = self._face(1, cluster_id=1)
        hidden = self._face(2, bbox=(40, 10, 20, 20), status="unknown_person")
        ambiguous = self._face(3, bbox=(70, 10, 20, 20))
        self._face(4, bbox=(70, 10, 20, 20))
        remote_name = Assignment("person", str(uuid4()), "Remote", provenance="confirmed")
        self._start()
        self.assertEqual(self._apply(self._remote((named, remote_name), (hidden, remote_name), (ambiguous, remote_name))), 0)
        self.assertEqual(self._state(), [
            (1, "active", "Local"), (2, "unknown_person", None), (3, "active", None), (4, "active", None),
        ])

    def test_stale_verified_path_and_mixed_private_location_prevent_application(self):
        box = self._face(1)
        self._add_location(self.root / "copy.jpg")
        remote = self._remote((box, Assignment("archived", archive_status="not_face", provenance="confirmed")),
                              paths=(SOURCE, "copy.jpg"))
        self._start()
        self.assertEqual(self._apply(remote, verified_hash="b" * 64), 0)
        self.connection.execute("DELETE FROM image_location WHERE filename = ?", (SOURCE,))
        self.assertEqual(self._apply(remote), 0)
        self._add_location(self.root.parent / "private" / "vacation.jpg")
        self.assertEqual(self._apply(remote, relative_path="copy.jpg"), 0)
        self.assertEqual(self._state(), [(1, "active", None)])

    def test_explicit_shared_unassignment_blocks_later_named_snapshot(self):
        box = self._face(1)
        unassigned = self._remote((box, Assignment("unassigned", provenance="confirmed")))
        named = self._remote((box, Assignment("person", str(uuid4()), "Anna", provenance="confirmed")))
        self._start()
        self.assertEqual(self._apply(unassigned), 1)
        self.assertEqual(self._apply(named), 0)
        self.assertEqual(self._state(), [(1, "active", None)])

    def test_later_same_peer_corrects_the_exact_untouched_face_and_records_high_water(self):
        box = self._face(1)
        other = self._face(2, bbox=(40, 10, 20, 20))
        anna = Assignment("person", str(uuid4()), "Anna", provenance="confirmed")
        bea = Assignment("person", str(uuid4()), "Bea", provenance="confirmed")
        remote = self._remote((box, anna), (other, anna))
        corrected = self._correct(remote, bea)
        self._start()
        self.assertEqual(self._observe(remote, 2), 2)
        self._start()
        self.assertEqual(self._observe(corrected, 4, producer="3" * 32), 0)
        self.assertEqual(self._observe(corrected, 2), 0)
        self.assertEqual(self._observe(corrected, 4), 1)
        self.assertEqual(self._state(), [(1, "active", "Bea"), (2, "active", "Anna")])
        self.assertEqual(self._observe(remote, 3), 0)
        self.assertEqual(self._observe(corrected, 4), 0)
        self.assertFalse(self._compare(corrected).conflicts)
        self.assertEqual(self._observe(self._correct(corrected, Assignment("unassigned", provenance="confirmed")), 5), 1)
        self.assertEqual(self._state(), [(1, "active", None), (2, "active", "Anna")])

    def test_no_decision_advances_peer_witness_without_removing_existing_assignment(self):
        box = self._face(1)
        remote = self._remote((box, Assignment("person", str(uuid4()), "Anna", provenance="confirmed")))
        older = self._correct(remote, Assignment("archived", archive_status="unknown_person", provenance="confirmed"))
        self._start()
        self.assertEqual(self._observe(remote, 1), 1)
        self.assertEqual(self._observe(self._correct(remote, None), 3), 0)
        self.assertEqual(self._observe(older, 2), 0)
        self.assertEqual(self._state(), [(1, "active", "Anna")])

    def test_local_face_touch_or_observable_away_and_back_permanently_fences_auto_correction(self):
        box = self._face(1)
        other_box = self._face(2, bbox=(40, 10, 20, 20))
        anna = Assignment("person", str(uuid4()), "Anna", provenance="confirmed")
        remote = self._remote((box, anna), (other_box, anna))
        shared_archive = Assignment("archived", archive_status="not_face", provenance="confirmed")
        changed = self._correct(remote, shared_archive)
        changed["faces"][1]["decision"] = asdict(shared_archive)
        self._start()
        self.assertEqual(self._observe(remote, 1), 2)
        self._start()
        # These operations end with precisely the original assignment values.
        self.connection.execute("UPDATE face SET review_status = review_status WHERE id = 1")
        self.connection.execute("UPDATE person SET name = 'Changed' WHERE name = 'Anna'")
        self.connection.execute("UPDATE person SET name = 'Anna' WHERE name = 'Changed'")
        self._start()
        self.assertEqual(self._observe(changed, 2), 0)
        self.assertEqual(self._state(), [(1, "active", "Anna"), (2, "active", "Anna")])
        self.assertEqual(len(self._compare(changed).conflicts), 2)

    def test_manual_keep_local_fences_a_later_remote_change_without_touching_face_columns(self):
        box = self._face(1)
        remote = self._remote((box, Assignment("person", str(uuid4()), "Anna", provenance="confirmed")))
        correction = self._correct(remote, Assignment("archived", archive_status="not_face", provenance="confirmed"))
        self._start()
        self.assertEqual(self._observe(remote, 1), 1)
        conflict, = self._compare(correction).conflicts
        self.assertTrue(self._resolve(correction, conflict, "local"))
        self.assertEqual(self._observe(correction, 2), 0)
        self.assertEqual(self._state(), [(1, "active", "Anna")])

    def test_legacy_receipt_has_no_provenance_and_ambiguous_regeneration_is_not_correction(self):
        box = self._face(1)
        other = self._face(2, bbox=(40, 10, 20, 20))
        named = Assignment("person", str(uuid4()), "Anna", provenance="confirmed")
        archived = Assignment("archived", archive_status="not_face", provenance="confirmed")
        legacy = self._remote((box, named))
        tracked = self._remote((other, named))
        self._start()
        self.assertEqual(self._apply(legacy), 1)
        self.assertEqual(self._observe(tracked, 1), 1)
        self.assertEqual(self._observe(self._correct(legacy, archived), 2), 0)
        changed_face = self._correct(tracked, archived)
        changed_face["faces"][0]["face_id"] = str(uuid4())
        self.assertEqual(self._observe(changed_face, 3), 0)
        self.assertEqual(self._state(), [(1, "active", "Anna"), (2, "active", "Anna")])

    def test_picking_shared_archive_moves_only_the_reviewed_face_and_rolls_back(self):
        self.connection.execute("INSERT INTO person(id,name) VALUES (1,'Local')")
        self.connection.execute("INSERT INTO cluster(id,person_id) VALUES (1,1)")
        box = self._face(1, cluster_id=1)
        self._face(2, bbox=(40, 10, 20, 20), cluster_id=1)
        remote = self._remote((box, Assignment("archived", archive_status="not_face", provenance="confirmed")))
        self._start()
        conflict, = self._compare(remote).conflicts
        self.assertTrue(self._resolve(remote, conflict, "shared"))
        self.assertEqual(self._state(), [(1, "not_face", None), (2, "active", "Local")])
        self.assertFalse(self._compare(remote).conflicts)
        self.connection.rollback()
        self.assertEqual(self._state(), [(1, "active", "Local"), (2, "active", "Local")])

    def test_keeping_local_persists_exact_case_but_reopens_when_the_remote_side_changes(self):
        self.connection.execute("INSERT INTO person(id,name) VALUES (1,'Local')")
        self.connection.execute("INSERT INTO cluster(id,person_id) VALUES (1,1)")
        box = self._face(1, cluster_id=1)
        remote = self._remote((box, Assignment("archived", archive_status="not_face", provenance="confirmed")))
        self._start()
        conflict, = self._compare(remote).conflicts
        self.assertTrue(self._resolve(remote, conflict, "local"))
        self._start()
        self.assertEqual(self._state(), [(1, "active", "Local")])
        self.assertFalse(self._compare(remote).conflicts)
        with self.assertRaises(SharedConflictChanged):
            self._resolve(remote, conflict, "local")
        remote["faces"][0]["decision"]["archive_status"] = "unknown_person"
        changed, = self._compare(remote).conflicts
        self.assertEqual(changed.shared[0].decision.archive_status, "unknown_person")

    def test_stale_local_decision_wrong_pair_or_unverified_path_is_rejected(self):
        self.connection.execute("INSERT INTO person(id,name) VALUES (1,'Local')")
        self.connection.execute("INSERT INTO cluster(id,person_id) VALUES (1,1)")
        box = self._face(1, cluster_id=1)
        remote = self._remote((box, Assignment("archived", archive_status="not_face", provenance="confirmed")))
        self._start()
        conflict, = self._compare(remote).conflicts
        for changes in ({"shared_face_id": str(uuid4())}, {"verified_relative_path": "old.jpg"}):
            with self.subTest(changes=changes), self.assertRaises(SharedConflictChanged):
                self._resolve(remote, conflict, "shared", **changes)
        self.connection.execute("UPDATE person SET name = 'Changed' WHERE id = 1")
        with self.assertRaises(SharedConflictChanged):
            self._resolve(remote, conflict, "shared")
        self.assertEqual(self._state(), [(1, "active", "Changed")])

    def test_same_name_shared_choice_stays_open_and_keep_local_records_unassignment(self):
        self.connection.execute("INSERT INTO person(id,name) VALUES (1,'Anna')")
        box = self._face(1)
        remote = self._remote((box, Assignment("person", str(uuid4()), "anna", provenance="confirmed")))
        self._start()
        conflict, = self._compare(remote).conflicts
        with self.assertRaises(SharedConflictBlocked):
            self._resolve(remote, conflict, "shared")
        self.assertEqual(self._state(), [(1, "active", None)])
        self.assertTrue(self._resolve(remote, conflict, "local"))
        local = capture_image(self.connection, self.root, DEVICE, 1)
        mapped = scoped_snapshot(self.connection, scope=SCOPE, device_id=DEVICE, local=local)
        self.assertEqual(mapped.faces[0].decision.kind, "unassigned")
        self.assertFalse(self._compare(remote).conflicts)

    def test_conflict_button_preflight_never_creates_links_or_merges_equal_names(self):
        self.connection.execute("INSERT INTO person(id,name) VALUES (1,'Anna')")
        left = FaceEvidence(str(uuid4()), HASH, (0.1, 0.1, 0.3, 0.3))
        self._start()

        def available(decision):
            right = FaceEvidence(str(uuid4()), HASH, left.bbox, decision)
            return is_conflict_person_available(self.connection, scope=SCOPE, left=left, right=right)

        self.assertFalse(is_conflict_person_available(self.connection, scope=SCOPE, left=left, right=None))
        self.assertFalse(available(None))
        self.assertFalse(available(Assignment("person", str(uuid4()), "anna", provenance="confirmed")))
        self.assertFalse(available(Assignment("person", person_name="Bea", provenance="legacy")))
        self.assertTrue(available(Assignment("person", str(uuid4()), "Bea", provenance="confirmed")))
        self.assertTrue(available(Assignment("archived", archive_status="not_face", provenance="confirmed")))
        self.assertEqual(self.connection.execute("SELECT name FROM person ORDER BY id").fetchone()[0], "Anna")
        self.assertIsNone(self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'shared_sync_person_link'"
        ).fetchone())

    def test_shared_choice_uses_a_previously_linked_person_without_renaming_them(self):
        self.connection.execute("INSERT INTO person(id,name) VALUES (1,'Local')")
        self.connection.execute("INSERT INTO cluster(id,person_id) VALUES (1,1)")
        first = self._face(1)
        second = self._face(2, bbox=(40, 10, 20, 20), cluster_id=1)
        anna = Assignment("person", str(uuid4()), "Anna", provenance="confirmed")
        self._start()
        self.assertEqual(self._apply(self._remote((first, anna))), 1)
        self.connection.execute("UPDATE person SET name = 'Anna (lokal)' WHERE name = 'Anna'")
        remote = self._remote((second, anna))
        conflict, = self._compare(remote).conflicts
        self.assertTrue(is_conflict_person_available(
            self.connection, scope=SCOPE, left=conflict.local[0], right=conflict.shared[0],
        ))
        self.assertTrue(self._resolve(remote, conflict, "shared"))
        self.assertEqual(self._state(), [(1, "active", "Anna (lokal)"), (2, "active", "Anna (lokal)")])
