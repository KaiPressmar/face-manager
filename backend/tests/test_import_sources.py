import hashlib
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from threading import RLock
from unittest.mock import Mock, patch

from fastapi import HTTPException

from backend.api.import_sources import _edit
from backend.db import schema
from backend.services.import_queue import ImportQueue
from backend.services.import_sources import ImportSources
from backend.services.photo_identity import file_identity


class ImportSourcesTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.root = self.directory / "Photos"
        self.root.mkdir()
        db = patch.object(schema, "DB_PATH", str(self.directory / "test.sqlite"))
        db.start()
        self.addCleanup(db.stop)
        schema.init_db(scan_sources=False)
        self.generation = 1
        self.queue = ImportQueue(Mock(), auto_start=False)
        self.enqueue = Mock()
        self.publish = Mock()
        self.changed = Mock()
        self.sources = ImportSources(
            library_lock=RLock(), generation=self.get_generation, is_idle=lambda: True,
            enqueue=self.enqueue, relocate=self.queue.relocate_source,
            suffixes={".jpg", ".jpeg", ".png"}, publish=self.publish, changed=self.changed,
        )
        self.source_id = self.sources.register(str(self.root))["id"]

    def get_generation(self, expected=None):
        if expected is not None and expected != self.generation:
            raise HTTPException(status_code=409)
        return self.generation

    def add_photo(self, path, content=b"photo bytes"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        digest = hashlib.sha256(content).hexdigest()
        with closing(schema.get_conn()) as conn, conn:
            image_id = conn.execute(
                """INSERT INTO image(path, directory, filename, content_hash, processed_at)
                   VALUES (?, ?, ?, ?, 'processed')""",
                (str(path), str(path.parent), path.name, digest),
            ).lastrowid
            conn.execute(
                """INSERT INTO image_location(image_id, path, directory, filename,
                   file_device, file_inode, file_size, modified_at_ns, file_changed_at_ns)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (image_id, str(path), str(path.parent), path.name, *file_identity(path.stat())),
            )
        return image_id

    def run_next(self):
        job = self.sources._next()
        self.assertIsNotNone(job)
        self.sources._scan(*job)
        return self.sources.list()[0]

    def test_discoveries_report_new_files_and_reuse_digests(self):
        self.add_photo(self.root / "known.jpg")
        new = self.root / "New folder" / "new.jpg"
        new.parent.mkdir()
        new.write_bytes(b"new")
        (new.parent / "notes.txt").write_text("Not a photo")
        (new.parent / "copy.jpg").write_bytes(b"photo bytes")
        self.sources.schedule(self.source_id)
        result = self.run_next()
        self.assertEqual((result["scanned_files"], result["new_files"], result["new_folders"]), (3, 1, 1))
        self.enqueue.assert_not_called()
        self.sources.schedule(self.source_id, import_new=True)
        with patch("backend.services.source_reconciliation._read_digest", side_effect=AssertionError("unchanged file was rehashed")):
            result = self.run_next()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["new_folders"], 0)
        self.enqueue.assert_called_once_with(str(self.root), retry_paths=[{"path": str(new), "kind": "image_unavailable"}])

    def test_detect_move_between_subdirectories_with_face_and_album_retained(self):
        original = self.root / "A" / "photo.jpg"
        image_id = self.add_photo(original)
        with closing(schema.get_conn()) as conn, conn:
            conn.execute("INSERT INTO face(image_id,bbox_x,bbox_y,bbox_w,bbox_h) VALUES (?,0,0,1,1)", (image_id,))
            conn.execute("INSERT INTO album(id,name,created_at,updated_at) VALUES ('album','Trip','now','now')")
            conn.execute("INSERT INTO album_photo(album_id,image_id,position) VALUES ('album',?,0)", (image_id,))
        moved = self.root / "B" / "renamed.jpg"
        moved.parent.mkdir()
        original.rename(moved)
        original.parent.rmdir()
        self.sources.schedule(self.source_id)
        result = self.run_next()
        self.assertEqual((result["status"], result["relinked_files"], result["unavailable_files"]), ("completed", 1, 0))
        self.changed.assert_called_once()
        with closing(schema.get_conn()) as conn:
            self.assertEqual(tuple(conn.execute("SELECT image_id,path FROM image_location").fetchone()), (image_id, str(moved)))
            self.assertEqual(conn.execute("SELECT path FROM image WHERE id = ?", (image_id,)).fetchone()[0], str(moved))
            self.assertEqual(conn.execute("SELECT image_id FROM face").fetchone()[0], image_id)
            self.assertEqual(conn.execute("SELECT image_id FROM album_photo").fetchone()[0], image_id)

    def test_ambiguous_locations_are_never_relinked_or_imported(self):
        original = self.root / "old.jpg"
        self.add_photo(original)
        original.unlink()
        for name in ("one.jpg", "two.jpg"):
            (self.root / name).write_bytes(b"photo bytes")
        self.sources.update(self.source_id, auto_scan=True, auto_import=True)
        self.sources.schedule(self.source_id)
        result = self.run_next()
        self.assertEqual((result["conflicts"], result["relinked_files"], result["unavailable_files"]), (2, 0, 1))
        self.enqueue.assert_not_called()
        selected = self.root / "two.jpg"
        self.assertIn(str(selected), result["conflict_candidates"][0]["candidate_paths"])
        self.sources.resolve(self.source_id, str(original), str(selected))
        result = self.run_next()
        self.assertEqual(result["relinked_files"], 1)
        self.assertFalse(result["conflict_candidates"])
        with closing(schema.get_conn()) as conn:
            self.assertEqual(conn.execute("SELECT path FROM image_location").fetchone()[0], str(selected))

    def test_preview_requirements_and_atomic_folder_relocation(self):
        original = self.root / "A" / "photo.jpg"
        self.add_photo(original)
        job = self.queue.enqueue(str(self.root))
        destination = self.directory / "New place"
        self.root.rename(destination)
        self.sources.relink(self.source_id, str(destination), confirm=False)
        preview = self.run_next()
        self.assertEqual((preview["status"], preview["folder_path"], preview["relinked_files"]), ("preview_ready", str(self.root), 1))
        with self.assertRaises(ValueError):
            self.sources.relink(self.source_id, str(self.directory / "wrong"), confirm=True)
        self.sources.relink(self.source_id, str(destination), confirm=True)
        result = self.run_next()
        self.assertEqual((result["status"], result["folder_path"], result["pending_path"]), ("completed", str(destination), None))
        self.assertEqual(self.queue.snapshot()["jobs"][0]["id"], job["id"])
        self.assertEqual(self.queue.snapshot()["jobs"][0]["folder_path"], str(destination))
        with closing(schema.get_conn()) as conn:
            self.assertEqual(conn.execute("SELECT path FROM image_location").fetchone()[0], str(destination / "A" / "photo.jpg"))

    def test_offline_and_cancelled_previews_keep_the_source(self):
        self.add_photo(self.root / "known.jpg")
        self.sources.schedule(self.source_id)
        self.assertIsNotNone(self.run_next()["last_success_at"])
        elsewhere = self.directory / "Offline"
        self.root.rename(elsewhere)
        self.sources.schedule(self.source_id)
        offline = self.run_next()
        self.assertEqual(offline["status"], "offline")
        self.assertEqual(offline["folder_path"], str(self.root))
        self.assertIsNotNone(offline["last_success_at"])
        self.sources.relink(self.source_id, str(elsewhere), confirm=False)
        self.assertEqual(self.run_next()["status"], "preview_ready")
        result = self.sources.cancel_preview(self.source_id)
        self.assertEqual((result["pending_path"], result["folder_path"]), (None, str(self.root)))

    def test_cancelled_import_remains_blocked_when_history_is_cleared(self):
        new = self.root / "new.jpg"
        new.write_bytes(b"new")
        self.sources.update(self.source_id, auto_scan=True, auto_import=True)
        job = self.queue.enqueue(str(self.root))
        self.queue.cancel(job["id"])
        self.sources.block_automatic_import(job["id"])
        self.queue.clear_history()
        self.sources.schedule(self.source_id)
        self.assertTrue(self.run_next()["auto_import_blocked"])
        self.enqueue.assert_not_called()
        self.sources.update(self.source_id, auto_scan=None, auto_import=True)
        self.sources.schedule(self.source_id)
        self.run_next()
        self.enqueue.assert_called_once()

    def test_replacement_during_scan_discards_stale_results(self):
        self.add_photo(self.root / "known.jpg")
        (self.root / "known.jpg").rename(self.root / "moved.jpg")
        self.sources.schedule(self.source_id)
        job = self.sources._next()
        self.generation += 1
        self.sources._scan(*job)
        self.changed.assert_not_called()
        with closing(schema.get_conn()) as conn:
            self.assertEqual(conn.execute("SELECT filename FROM image_location").fetchone()[0], "known.jpg")
        self.sources.recover_interrupted_scans()
        self.assertEqual(self.sources.list()[0]["status"], "queued")
        self.assertEqual(self.run_next()["relinked_files"], 1)

    def test_changed_destination_does_not_overwrite_the_only_location(self):
        original = self.root / "known.jpg"
        self.add_photo(original)
        original.rename(self.root / "moved.jpg")
        self.sources.schedule(self.source_id)
        with patch("backend.services.import_sources.matches_file_identity", return_value=False):
            result = self.run_next()
        self.assertEqual((result["relinked_files"], result["unavailable_files"], result["conflicts"]), (0, 1, 1))
        with closing(schema.get_conn()) as conn:
            self.assertEqual(conn.execute("SELECT path FROM image_location").fetchone()[0], str(original))

    def test_existing_import_roots_are_loaded_once_without_recreating_removed_entries(self):
        self.queue.enqueue(str(self.directory / "Older folder"))
        with closing(schema.get_conn()) as conn, conn:
            conn.execute("DROP TABLE import_source_file")
            conn.execute("DROP TABLE import_source")
        schema.init_db(scan_sources=False)
        old = self.sources.list()[0]
        self.assertEqual(old["folder_path"], str(self.directory / "Older folder"))
        self.assertFalse(old["auto_scan"])
        self.sources.remove(old["id"])
        schema.init_db(scan_sources=False)
        self.assertEqual(self.sources.list(), [])

    def test_library_token_rejects_stale_dialog(self):
        with patch("backend.api.import_sources.get_person_suggestion_epoch", return_value="current"):
            with self.assertRaises(HTTPException) as stale:
                with _edit("previous"):
                    self.fail("A stale dialog could mutate the source registry")
            self.assertEqual(stale.exception.status_code, 409)
