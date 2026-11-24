import sqlite3
import tempfile
import threading
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
    def test_startup_only_counts_unavailable_paths_and_manual_keeps_offline_volumes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            offline = root / "external-drive" / "photo.jpg"
            deleted = root / "deleted.jpg"
            with patch.object(schema, "DB_PATH", str(root / "database.sqlite")):
                schema.init_db()
                conn = schema.get_conn()
                person = conn.execute("INSERT INTO person(name) VALUES ('Known person')").lastrowid
                cluster = conn.execute("INSERT INTO cluster(person_id) VALUES (?)", (person,)).lastrowid
                for path in (offline, deleted):
                    image_id = conn.execute(
                        "INSERT INTO image(path, directory, filename) VALUES (?, ?, ?)",
                        (str(path), str(path.parent), path.name),
                    ).lastrowid
                    conn.execute(
                        "INSERT INTO image_location(image_id, path, directory, filename) VALUES (?, ?, ?, ?)",
                        (image_id, str(path), str(path.parent), path.name),
                    )
                    conn.execute("INSERT INTO face(image_id, cluster_id) VALUES (?, ?)", (image_id, cluster))
                conn.commit()
                conn.close()

                for reason, removed in (("startup", 0), ("manual", 1)):
                    with self.subTest(reason=reason):
                        finished = threading.Event()
                        cleanup = ImagePathCleanup(
                            is_idle=lambda: True,
                            on_change=lambda state: finished.set() if state["status"] == "completed" else None,
                            check_delay_seconds=0,
                        )
                        cleanup.start(reason)
                        self.assertTrue(finished.wait(2))
                        self.assertTrue(cleanup.stop())
                        self.assertEqual(cleanup.snapshot()["unavailable_paths"], 2)
                        self.assertEqual(cleanup.snapshot()["removed_paths"], removed)
                        conn = schema.get_conn()
                        rows = conn.execute(
                            "SELECT image.path, face.cluster_id FROM image JOIN face ON face.image_id = image.id"
                        ).fetchall()
                        conn.close()
                        self.assertEqual(dict(rows), {
                            str(path): cluster for path in ((offline, deleted) if not removed else (offline,))
                        })

    def test_resume_after_stop_timeout_waits_for_existing_worker(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            photo = root / "photo.jpg"
            with patch.object(schema, "DB_PATH", str(root / "database.sqlite")):
                schema.init_db()
                conn = schema.get_conn()
                image_id = conn.execute(
                    "INSERT INTO image(path, directory, filename) VALUES (?, ?, ?)",
                    (str(photo), str(root), photo.name),
                ).lastrowid
                conn.execute(
                    "INSERT INTO image_location(image_id, path, directory, filename) VALUES (?, ?, ?, ?)",
                    (image_id, str(photo), str(root), photo.name),
                )
                conn.commit()
                conn.close()

                entered, release = threading.Event(), threading.Event()

                def slow_path_check(_path):
                    entered.set()
                    release.wait(2)
                    return False

                noticed_idle = threading.Event()
                cleanup = ImagePathCleanup(
                    is_idle=lambda: True,
                    on_change=lambda state: noticed_idle.set() if state["status"] == "idle" else None,
                    check_delay_seconds=0,
                )
                with patch("backend.services.image_path_cleanup.os.path.isfile", side_effect=slow_path_check):
                    cleanup.start("manual")
                    worker = cleanup._worker
                    try:
                        self.assertTrue(entered.wait(1))
                        self.assertFalse(cleanup.stop(timeout=0))
                        cleanup.resume()
                        self.assertTrue(cleanup._stop_event.is_set())
                    finally:
                        release.set()
                        worker.join(2)
                self.assertFalse(worker.is_alive())
                self.assertFalse(cleanup._stop_event.is_set())
                self.assertTrue(noticed_idle.is_set())
                self.assertEqual(cleanup.snapshot()["status"], "idle")
                self.assertEqual(cleanup.snapshot()["removed_paths"], 0)

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
