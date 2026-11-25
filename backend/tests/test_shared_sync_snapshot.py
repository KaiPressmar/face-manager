import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.db import schema
from backend.services.shared_sync_snapshot import MAX_SNAPSHOT_PAGE, capture_image, capture_library


DIGEST = "a9" * 32


class SharedSyncSnapshotTest(unittest.TestCase):
    def setUp(self):
        temp = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(temp) / "shared"
        self.enterContext(patch.object(schema, "DB_PATH", str(Path(temp) / "library.sqlite")))
        schema.init_db(scan_sources=False)
        self.conn = schema.get_conn()
        self.addCleanup(self.conn.close)

    def add_photo(self, image_id, *paths, digest=DIGEST, geometry=(200, 100, 6), processed=True):
        stored = str(paths[0] if paths else self.root / f"{image_id}.jpg")
        self.conn.execute(
            """
            INSERT INTO image(id,path,directory,filename,content_hash,processed_at,raw_width,raw_height,exif_orientation)
            VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (image_id, stored, str(Path(stored).parent), Path(stored).name,
             digest, "done" if processed else None, *geometry),
        )
        self.conn.executemany(
            "INSERT INTO image_location(image_id,path,directory,filename) VALUES(?,?,?,?)",
            [(image_id, str(path), str(Path(path).parent), Path(path).name) for path in paths],
        )

    def add_face(self, face_id, image_id, status="active", bbox=(20, 10, 40, 30), cluster_id=None):
        self.conn.execute(
            """
            INSERT INTO face(id,image_id,bbox_x,bbox_y,bbox_w,bbox_h,review_status,cluster_id,embedding)
            VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (face_id, image_id, *bbox, status, cluster_id, b"legacy unknown embedding"),
        )

    def read_snapshot(self, **kwargs):
        self.conn.commit()
        self.conn.execute("BEGIN")
        return capture_library(self.conn, self.root, "device-a", **kwargs)

    def test_read_snapshot_preserves_reliable_boxes_and_explicit_decisions(self):
        self.add_photo(5, self.root / "Unterordner" / "Urlaub.jpg", self.root / "Kopie.jpg")
        self.conn.execute("INSERT INTO person(id,name) VALUES(1,'Anna')")
        self.conn.execute("INSERT INTO cluster(id,person_id) VALUES(1,1)")
        self.add_face(4, 5, cluster_id=1)
        self.add_face(5, 5, "unknown_person")
        self.add_face(6, 5, "not_face")
        self.add_face(7, 5)
        self.add_face(8, 5, bbox=(-2, 30, 60, 35), cluster_id=1)

        with patch("pathlib.Path.exists", side_effect=AssertionError("source was accessed")):
            page = self.read_snapshot()
        record, = page.records
        self.assertIsNone(page.next_image_id)
        self.assertEqual(page.skipped_images, 0)
        self.assertEqual(record.relative_paths, ("Kopie.jpg", "Unterordner/Urlaub.jpg"))
        self.assertEqual((record.raw_width, record.raw_height, record.exif_orientation), (200, 100, 6))
        named, unknown, not_face, unassigned, out_of_bounds = record.faces
        self.assertEqual(named.bbox, (0.1, 0.1, 0.3, 0.4))
        self.assertTrue(named.photo_verified and named.geometry_reliable)
        self.assertEqual(named.decision.person_name, "Anna")
        self.assertEqual(named.decision.provenance, "legacy")
        self.assertNotEqual(named.face_id, named.decision.person_key)
        self.assertEqual(unknown.decision.archive_status, "unknown_person")
        self.assertEqual(not_face.decision.archive_status, "not_face")
        self.assertIsNone(unassigned.decision)
        self.assertIsNone(out_of_bounds.bbox)
        self.assertFalse(out_of_bounds.geometry_reliable)
        self.assertEqual(out_of_bounds.decision.person_key, named.decision.person_key)
        self.assertFalse(hasattr(named, "embedding"))

        self.assertEqual(capture_image(self.conn, self.root, "device-a", 5), record)
        other_device = capture_image(self.conn, self.root, "device-b", 5)
        self.assertNotEqual(other_device.faces[0].face_id, named.face_id)
        self.assertNotEqual(other_device.faces[0].decision.person_key, named.decision.person_key)

    def test_skipped_pages_and_dirty_capture_keep_scope_private(self):
        self.add_photo(1, self.root.parent / "shared-extra" / "Sibling.jpg", digest="1" * 64)
        self.add_photo(2, self.root / "Mixed.jpg", self.root.parent / "private" / "Mixed.jpg", digest="2" * 64)
        self.add_photo(3, self.root / "NoHash.jpg", digest=None)
        self.add_photo(4, self.root / "Unprocessed.jpg", digest="4" * 64, processed=False)
        self.add_photo(5, self.root / "nested" / "Visible.jpg", digest=DIGEST.upper(), geometry=(None, None, None))
        self.add_face(31, 5, "unknown_person")
        self.add_photo(6)
        self.add_photo(7, self.root / ".fm" / "Internal.jpg", digest="7" * 64)

        first = self.read_snapshot(limit=2)
        self.assertEqual((first.records, first.next_image_id, first.skipped_images), ((), 2, 2))
        second = capture_library(self.conn, self.root, "device-a", after_image_id=2, limit=2)
        self.assertEqual((second.next_image_id, second.skipped_images), (5, 1))
        record, = second.records
        self.assertEqual(record.photo_sha256, DIGEST)
        self.assertEqual(record.faces[0].decision.archive_status, "unknown_person")
        self.assertFalse(record.faces[0].geometry_reliable)
        last = capture_library(self.conn, self.root, "device-a", after_image_id=5)
        self.assertEqual((last.records, last.next_image_id, last.skipped_images), ((), None, 2))
        self.assertIsNone(capture_image(self.conn, self.root, "device-a", 2))
        self.assertIsNone(capture_image(self.conn, self.root, "device-a", 4))

    def test_snapshot_rejects_nonportable_and_traversal_path_records(self):
        self.add_photo(1, str(self.root) + "/nested/../Unsafe.jpg", digest="1" * 64)
        self.add_photo(2, self.root / "NUL.jpg", digest="2" * 64)
        self.add_photo(3, self.root / "unsafe:stream.jpg", digest="3" * 64)
        self.add_photo(4, self.root / "Trailing.jpg ", digest="4" * 64)
        page = self.read_snapshot()
        self.assertEqual((page.records, page.skipped_images), ((), 4))

    def test_paging_uses_caller_transaction_without_committing(self):
        self.add_photo(1, self.root / "First.jpg", digest="1" * 64)
        self.add_photo(2, self.root / "Second.jpg", digest="2" * 64)
        first = self.read_snapshot(limit=1)
        self.assertTrue(self.conn.in_transaction)
        self.assertEqual(first.next_image_id, 1)
        concurrent = schema.get_conn()
        try:
            concurrent.execute("UPDATE image SET processed_at = NULL WHERE id = 2")
            concurrent.commit()
        finally:
            concurrent.close()
        second = capture_library(self.conn, self.root, "device-a", after_image_id=first.next_image_id, limit=1)
        self.assertEqual([item.image_id for item in second.records], [2])
        self.conn.rollback()
        with self.assertRaisesRegex(ValueError, "caller-owned"):
            capture_library(self.conn, self.root, "device-a")
        self.conn.execute("BEGIN")
        self.assertEqual(capture_library(self.conn, self.root, "device-a", after_image_id=1).records, ())
        with self.assertRaises(ValueError):
            capture_library(self.conn, self.root, "device-a", limit=MAX_SNAPSHOT_PAGE+1)


if __name__ == "__main__":
    unittest.main()
