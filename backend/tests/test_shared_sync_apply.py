import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from backend.db import schema
from backend.services.shared_sync_apply import apply_unambiguous, compare_for_scope, scoped_snapshot
from backend.services.shared_sync_merge import Assignment, FaceEvidence
from backend.services.shared_sync_reconcile import photo_event
from backend.services.shared_sync_snapshot import SnapshotPhoto, capture_image


HASH = "a9" * 32
SOURCE = "vacation.jpg"
SCOPE = "library/current-generation"
DEVICE = "receiving-device"


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

    def _apply(self, remote, *, scope=SCOPE, relative_path=SOURCE, verified_hash=HASH):
        return apply_unambiguous(
            self.connection, scope=scope, source_path=self.root, device_id=DEVICE, image_id=1,
            remote=remote, verified_hash=verified_hash, verified_relative_path=relative_path,
        )

    def _start(self):
        self.connection.commit()
        self.connection.execute("BEGIN IMMEDIATE")

    def _state(self):
        return [tuple(row) for row in self.connection.execute("""
            SELECT f.id, f.review_status, p.name FROM face f
            LEFT JOIN cluster c ON f.cluster_id = c.id LEFT JOIN person p ON c.person_id = p.id
            ORDER BY f.id
        """)]

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
