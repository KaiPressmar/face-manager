import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.db import schema
from backend.services.shared_sync_outbox import (
    acknowledge_changes,
    enqueue_baseline_page,
    initialize_outbox,
    pending_changes,
    register_scope,
    retire_scope,
)
from backend.services.shared_sync_snapshot import capture_image


class SharedSyncOutboxTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name) / "shared"
        database = patch.object(schema, "DB_PATH", str(Path(directory.name) / "library.sqlite"))
        database.start()
        self.addCleanup(database.stop)
        schema.init_db(scan_sources=False)
        self.conn = schema.get_conn()
        self.addCleanup(self.conn.close)

    def photo(self, image_id, *, processed=True):
        path = self.root / f"{image_id}.jpg"
        self.conn.execute(
            """INSERT INTO image (id, path, directory, filename, content_hash, processed_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (image_id, str(path), str(path.parent), path.name, format(image_id, "064x"), "done" if processed else None),
        )
        self.location(image_id, path)

    def location(self, image_id, path):
        self.conn.execute(
            "INSERT INTO image_location (image_id, path, directory, filename) VALUES (?, ?, ?, ?)",
            (image_id, str(path), str(path.parent), path.name),
        )

    def initialize(self, *scopes):
        self.conn.execute("BEGIN IMMEDIATE")
        with self.conn:
            initialize_outbox(self.conn)
            for scope in scopes:
                register_scope(self.conn, scope)

    def pending(self, scope="scope-a", **page):
        self.conn.execute("BEGIN")
        with self.conn:
            return pending_changes(self.conn, scope, **page)

    def acknowledge(self, changes, scope="scope-a"):
        self.conn.execute("BEGIN IMMEDIATE")
        with self.conn:
            return acknowledge_changes(self.conn, scope, changes)

    def baseline(self, scope="scope-a", limit=64):
        self.conn.execute("BEGIN IMMEDIATE")
        with self.conn:
            return enqueue_baseline_page(self.conn, scope, limit=limit)

    def test_initial_baseline_resumes_and_preserves_newer_mutations(self):
        with self.conn:
            self.photo(1)
            self.photo(2, processed=False)
            self.photo(3)
        self.initialize("scope-a")
        self.assertTrue(self.baseline(limit=1))
        self.assertEqual([item.image_id for item in self.pending()], [1])
        with self.conn:
            self.conn.execute("UPDATE image SET processed_at = 'done' WHERE id = 2")
            self.conn.execute("UPDATE image SET raw_width = 80 WHERE id = 3")
            self.photo(4)
        pending_before = self.pending()
        self.conn.close()
        self.conn = schema.get_conn()
        self.addCleanup(self.conn.close)
        self.initialize("scope-a")
        self.assertTrue(self.baseline(limit=1))
        self.assertFalse(self.baseline(limit=1))
        self.assertEqual(self.pending(), pending_before)
        self.assertEqual([item.image_id for item in self.pending()], [1, 2, 3, 4])
        self.assertEqual(self.acknowledge(pending_before), 4)
        self.assertFalse(self.baseline())
        self.assertEqual(self.pending(), ())

    def test_receipts_are_persistent_revision_checked_and_scope_local(self):
        self.initialize("scope-a", "scope-b")
        with self.conn:
            self.photo(1)
        original, = self.pending()
        with self.conn:
            self.conn.execute("UPDATE image SET raw_width = 90 WHERE id = 1")
        revised, = self.pending()
        self.assertGreater(revised.revision, original.revision)
        self.assertEqual(self.acknowledge((original,)), 0)
        self.assertEqual(self.pending(), (revised,))
        self.assertEqual(self.acknowledge((revised,)), 1)
        self.assertEqual(self.acknowledge((revised,)), 0)
        self.assertEqual(self.pending("scope-b"), (revised,))
        with self.conn:
            self.conn.execute("UPDATE image SET raw_width = 75 WHERE id = 1")
        self.assertGreater(self.pending()[0].revision, revised.revision)

    def test_pending_scan_continues_past_deferred_items_with_shared_revision(self):
        with self.conn:
            for image_id in (1, 2, 3):
                self.photo(image_id)
        self.initialize("scope-a")
        self.assertFalse(self.baseline())
        first, = self.pending(limit=1)
        second, = self.pending(after=first, limit=1)
        third, = self.pending(after=second)
        self.assertEqual(first.revision, second.revision)
        self.assertEqual([first.image_id, second.image_id, third.image_id], [1, 2, 3])
        self.assertEqual(self.pending(after=third), ())
        self.assertEqual(self.acknowledge((second, third)), 2)
        self.assertEqual(self.pending(), (first,))
        with self.conn:
            self.conn.execute("UPDATE image SET raw_height = 100 WHERE id = 1")
        moved, = self.pending(after=third)
        self.assertEqual(moved.image_id, first.image_id)
        self.assertGreater(moved.revision, third.revision)

    def test_retired_scope_stops_receiving_changes_without_affecting_another_scope(self):
        self.initialize("scope-a", "scope-b")
        with self.conn:
            self.photo(1)
        pending = self.pending()
        self.conn.execute("BEGIN IMMEDIATE")
        with self.conn:
            retire_scope(self.conn, "scope-a")
            retire_scope(self.conn, "scope-a")
        with self.conn:
            self.photo(2)
        self.assertEqual(self.pending(), ())
        self.assertEqual(self.acknowledge(pending), 0)
        self.assertEqual([item.image_id for item in self.pending("scope-b")], [1, 2])

    def test_triggers_follow_face_review_person_rename_and_relationship_moves(self):
        self.initialize("scope-a")
        with self.conn:
            self.photo(1)
            self.photo(2)
            self.conn.execute("INSERT INTO person VALUES (1, 'Anna'), (2, 'Ben')")
            self.conn.execute("INSERT INTO cluster (id, person_id) VALUES (1, 1)")
            self.conn.execute("INSERT INTO face (id, image_id, cluster_id) VALUES (1, 1, 1)")
        self.acknowledge(self.pending())

        def expect_update(sql, expected):
            with self.conn:
                self.conn.execute(sql)
            changes = self.pending()
            self.assertEqual([item.image_id for item in changes], expected)
            self.acknowledge(changes)

        expect_update("UPDATE person SET name = 'Anna Beispiel' WHERE id = 1", [1])
        expect_update("UPDATE cluster SET person_id = 2 WHERE id = 1", [1])
        expect_update("UPDATE face SET review_status = 'not_face' WHERE id = 1", [1])
        expect_update("UPDATE face SET image_id = 2 WHERE id = 1", [1, 2])
        expect_update("UPDATE image_location SET modified_at_ns = 2 WHERE image_id = 1", [])
        expect_update("DELETE FROM face WHERE id = 1", [2])
        expect_update("DELETE FROM image WHERE id = 2", [2])

    def test_rollback_does_not_enqueue_and_mixed_paths_fail_closed(self):
        self.initialize("scope-a")
        self.conn.execute("BEGIN IMMEDIATE")
        self.photo(1)
        self.conn.rollback()
        self.assertEqual(self.pending(), ())

        with self.conn:
            self.photo(1)
        self.acknowledge(self.pending())
        private_path = self.root.parent / "private" / "1.jpg"
        with self.conn:
            self.location(1, private_path)
        self.conn.execute("BEGIN")
        with self.conn:
            self.assertEqual([item.image_id for item in pending_changes(self.conn, "scope-a")], [1])
            self.assertIsNone(capture_image(self.conn, self.root, "pc-a", 1))
        with self.conn:
            self.conn.execute("DELETE FROM image_location WHERE path = ?", (str(private_path),))
        self.conn.execute("BEGIN")
        with self.conn:
            visible = capture_image(self.conn, self.root, "pc-a", 1)
            self.assertEqual(visible.relative_paths, ("1.jpg",))


if __name__ == "__main__":
    unittest.main()
