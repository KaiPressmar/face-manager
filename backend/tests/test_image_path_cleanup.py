import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.db import schema
from backend.services.image_path_cleanup import ImagePathCleanup
from backend.services.storage import _descendant_filter


class FolderDescendantFilterTest(unittest.TestCase):
    def test_windows_parent_matches_descendants_with_special_characters(self):
        folder = r"C:\Family Photos\100%_Best"
        conditions, params = _descendant_filter([folder])
        self.assertEqual(len(conditions), 1)

        conn = sqlite3.connect(":memory:")
        try:
            matches = conn.execute(
                f"SELECT {conditions[0]} FROM (SELECT ? AS directory) location",
                (*params, r"C:\Family Photos\100%_Best\2026\photo.jpg"),
            ).fetchone()[0]
            sibling_matches = conn.execute(
                f"SELECT {conditions[0]} FROM (SELECT ? AS directory) location",
                (*params, r"C:\Family Photos\1000_Best\photo.jpg"),
            ).fetchone()[0]
        finally:
            conn.close()

        self.assertEqual(matches, 1)
        self.assertEqual(sibling_matches, 0)


class ImagePathCleanupTest(unittest.TestCase):
    def test_removes_missing_locations_and_images_without_locations(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "database.sqlite"
            available = root / "available.jpg"
            available.write_bytes(b"image")
            missing_duplicate = root / "missing duplicate.jpg"
            missing_only = root / "missing-only.jpg"

            with patch.object(schema, "DB_PATH", str(db_path)), patch(
                "backend.services.image_path_cleanup.get_conn", schema.get_conn
            ):
                schema.init_db()
                conn = schema.get_conn()
                first_id = conn.execute(
                    """
                    INSERT INTO image(path, directory, filename, processed_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (str(missing_duplicate), str(root), missing_duplicate.name),
                ).lastrowid
                second_id = conn.execute(
                    """
                    INSERT INTO image(path, directory, filename, processed_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (str(missing_only), str(root), missing_only.name),
                ).lastrowid
                conn.executemany(
                    """
                    INSERT INTO image_location(image_id, path, directory, filename)
                    VALUES (?, ?, ?, ?)
                    """,
                    [
                        (first_id, str(missing_duplicate), str(root), missing_duplicate.name),
                        (first_id, str(available), str(root), available.name),
                        (second_id, str(missing_only), str(root), missing_only.name),
                    ],
                )
                conn.commit()
                conn.close()

                removed_paths, removed_images, face_ids = ImagePathCleanup._remove_missing(
                    [str(missing_duplicate), str(missing_only)]
                )

                self.assertEqual((removed_paths, removed_images, face_ids), (2, 1, []))
                conn = schema.get_conn()
                remaining = conn.execute(
                    "SELECT path FROM image_location ORDER BY path"
                ).fetchall()
                canonical = conn.execute(
                    "SELECT path FROM image WHERE id = ?", (first_id,)
                ).fetchone()["path"]
                removed = conn.execute(
                    "SELECT 1 FROM image WHERE id = ?", (second_id,)
                ).fetchone()
                conn.close()

                self.assertEqual([row["path"] for row in remaining], [str(available)])
                self.assertEqual(canonical, str(available))
                self.assertIsNone(removed)

    def test_rechecks_a_path_before_deleting_it(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "database.sqlite"
            restored = root / "restored.jpg"
            restored.write_bytes(b"back")

            with patch.object(schema, "DB_PATH", str(db_path)), patch(
                "backend.services.image_path_cleanup.get_conn", schema.get_conn
            ):
                schema.init_db()
                conn = schema.get_conn()
                image_id = conn.execute(
                    """
                    INSERT INTO image(path, directory, filename, processed_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (str(restored), str(root), restored.name),
                ).lastrowid
                conn.execute(
                    """
                    INSERT INTO image_location(image_id, path, directory, filename)
                    VALUES (?, ?, ?, ?)
                    """,
                    (image_id, str(restored), str(root), restored.name),
                )
                conn.commit()
                conn.close()

                result = ImagePathCleanup._remove_missing([str(restored)])
                self.assertEqual(result, (0, 0, []))


if __name__ == "__main__":
    unittest.main()
