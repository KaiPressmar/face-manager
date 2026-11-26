import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from backend.db import schema
from backend.services.archive_repair import (
    apply_archive_repair,
    preview_archive_repair,
    register_archive_repair_snapshot,
    undo_archive_repair,
)


class ArchiveRepairTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.directory = Path(folder.name)
        self.database = self.directory / "library.sqlite"
        database_override = patch.object(schema, "DB_PATH", str(self.database))
        database_override.start()
        self.addCleanup(database_override.stop)
        schema.init_db(scan_sources=False)
        self.change("""
            INSERT INTO person(id, name) VALUES (1, 'Andrea'), (2, 'Bernhard');
            INSERT INTO cluster(id, label, person_id) VALUES (1, 'Andrea', 1), (2, 'Bernhard', 2);
            INSERT INTO image(id, path, directory, filename, content_hash, processed_at,
                              raw_width, raw_height, exif_orientation)
            VALUES (1, 'photo.jpg', '', 'photo.jpg', printf('%064d', 1), '2025-04-01 14:00:00', 500, 500, 1),
                   (2, 'other.jpg', '', 'other.jpg', printf('%064d', 2), '2025-04-01 14:00:03', 500, 500, 1);
            INSERT INTO face(id, image_id, cluster_id, bbox_x, bbox_y, bbox_w, bbox_h)
            VALUES (1, 1, 1, 15, 30, 80, 80), (2, 1, 1, 200, 30, 80, 80), (3, 2, 2, 50, 30, 80, 80);
        """)

    def change(self, sql):
        with closing(schema.get_conn()) as connection, connection:
            connection.executescript(sql)

    def faces(self, source=None):
        connector = sqlite3.connect(source) if source else schema.get_conn()
        with closing(connector) as connection:
            return [tuple(row) for row in connection.execute("SELECT id, cluster_id, review_status FROM face ORDER BY id")]

    def snapshot(self, name, *, folder="update-backups"):
        path = self.directory / folder / name
        path.parent.mkdir(exist_ok=True)
        with closing(schema.get_conn()) as connection, closing(sqlite3.connect(path)) as target:
            connection.backup(target)
            target.execute("PRAGMA journal_mode = DELETE")
        return path

    def test_startup_detaches_only_explicit_archives_and_keeps_recovery_copy(self):
        self.change("UPDATE face SET review_status = 'unknown_person' WHERE id = 1; "
                    "UPDATE face SET review_status = 'not_face' WHERE id = 3;")
        # A failed backup must not silently repair anything.
        with patch("backend.services.archive_repair._save_recovery_copy", side_effect=OSError("full")):
            with self.assertLogs("face_manager.archive_repair", "WARNING"):
                schema.init_db(scan_sources=False)
        self.assertEqual(self.faces(), [(1, 1, "unknown_person"), (2, 1, "active"), (3, 2, "not_face")])
        schema.init_db(scan_sources=False)
        schema.init_db(scan_sources=False)
        self.assertEqual(self.faces(), [(1, None, "unknown_person"), (2, 1, "active"), (3, None, "not_face")])
        self.assertEqual(preview_archive_repair()["automatic_repaired"], 2)
        snapshots = list((self.directory / "archive-repair-backups").glob("*.sqlite"))
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(self.faces(snapshots[0]), [(1, 1, "unknown_person"), (2, 1, "active"), (3, 2, "not_face")])

    def test_preview_confirmation_undo_and_stale_selection(self):
        self.change("UPDATE face SET review_status = 'unknown_person', cluster_id = NULL WHERE id = 1; "
                    "UPDATE face SET review_status = 'not_face', cluster_id = NULL WHERE id = 3;")
        self.snapshot("database-0.17.0-pre.sqlite")
        self.change("UPDATE face SET review_status = 'active', cluster_id = 1 WHERE id IN (1, 3);")
        preview = preview_archive_repair()
        self.assertEqual(preview["checked_backups"], 1)
        self.assertEqual(preview["skipped_conflicts"], 0)
        self.assertEqual([(row["face_id"], row["restore_status"]) for row in preview["candidates"]],
                         [(1, "unknown_person"), (3, "not_face")])
        self.assertEqual(self.faces(), [(1, 1, "active"), (2, 1, "active"), (3, 1, "active")])
        old_token = preview["candidates"][0]["token"]
        self.change("UPDATE face SET bbox_x = 16 WHERE id = 1;")
        with self.assertRaises(LookupError):
            apply_archive_repair([old_token])
        token = preview_archive_repair()["candidates"][0]["token"]
        applied = apply_archive_repair([token])
        self.assertEqual(applied["count"], 1)
        self.assertEqual(preview_archive_repair()["latest_batch"], applied)
        self.assertEqual(self.faces(), [(1, None, "unknown_person"), (2, 1, "active"), (3, 1, "active")])
        self.assertEqual(undo_archive_repair(applied["batch_id"]), applied)
        self.assertEqual(self.faces(), [(1, 1, "active"), (2, 1, "active"), (3, 1, "active")])
        with self.assertRaises(LookupError):
            undo_archive_repair(applied["batch_id"])

    def test_old_lineage_matches_but_ambiguous_and_conflicting_evidence_never_proposes(self):
        self.change("UPDATE face SET review_status = 'unknown_person', cluster_id = NULL WHERE id IN (1, 3);")
        legacy = self.snapshot("old-manual.sqlite", folder="repair-snapshots")
        with closing(sqlite3.connect(legacy)) as backup, backup:
            backup.execute("DELETE FROM app_settings WHERE key = 'archive_repair_library_lineage'")
        self.change("UPDATE face SET review_status = 'active', cluster_id = 1 WHERE id = 1; "
                    "UPDATE face SET review_status = 'active', cluster_id = 2 WHERE id = 3;")
        self.assertEqual(len(preview_archive_repair()["candidates"]), 2)

        self.change("INSERT INTO face(id, image_id, cluster_id, bbox_x, bbox_y, bbox_w, bbox_h) "
                    "VALUES (4, 1, 1, 16, 30, 80, 80);")
        self.snapshot("database-before-restore-conflicting.sqlite", folder="restore-backups")
        result = preview_archive_repair()
        self.assertEqual(result["checked_backups"], 2)
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["skipped_conflicts"], 3)

    def test_undo_refuses_to_overwrite_a_later_edit(self):
        self.change("UPDATE face SET review_status = 'unknown_person', cluster_id = NULL WHERE id = 1;")
        self.snapshot("database-0.17.0-pre.sqlite")
        self.change("UPDATE face SET review_status = 'active', cluster_id = 1 WHERE id = 1;")
        token = preview_archive_repair()["candidates"][0]["token"]
        batch = apply_archive_repair([token])
        self.change("UPDATE face SET review_status = 'active', cluster_id = 2 WHERE id = 1;")
        with self.assertRaises(LookupError):
            undo_archive_repair(batch["batch_id"])
        self.assertEqual(self.faces()[0], (1, 2, "active"))
        self.change("UPDATE face SET review_status = 'unknown_person', cluster_id = NULL WHERE id = 1;")
        with self.assertRaises(LookupError):
            undo_archive_repair(batch["batch_id"])
        self.assertEqual(self.faces()[0], (1, None, "unknown_person"))

    def test_other_library_and_inconsistent_raw_geometry_do_not_propose(self):
        self.change("UPDATE face SET review_status = 'not_face', cluster_id = NULL WHERE id = 1;")
        other = self.snapshot("database-0.17.0-other.sqlite")
        with closing(sqlite3.connect(other)) as backup, backup:
            backup.execute(
                "UPDATE app_settings SET value = 'other-lineage' WHERE key = 'archive_repair_library_lineage'"
            )
        self.change("UPDATE image SET exif_orientation = 3 WHERE id = 1;")
        self.snapshot("database-pre-share-before.sqlite", folder="shared-library-backups")
        self.change("UPDATE face SET review_status = 'active', cluster_id = 1 WHERE id = 1; "
                    "UPDATE image SET exif_orientation = 1 WHERE id = 1;")
        result = preview_archive_repair()
        self.assertEqual(result["checked_backups"], 1)
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["skipped_conflicts"], 0)

    def test_registers_an_exported_backup_without_replacing_current_library(self):
        self.change("UPDATE face SET review_status = 'unknown_person', cluster_id = NULL WHERE id = 1;")
        manual = self.snapshot("my-export.sqlite", folder="outside-managed-snapshots")
        self.change("UPDATE face SET review_status = 'active', cluster_id = 1 WHERE id = 1; "
                    "UPDATE image SET filename = 'new name.jpg' WHERE id = 1;")
        payload = manual.read_bytes()
        self.assertEqual(preview_archive_repair()["checked_backups"], 0)
        registered = register_archive_repair_snapshot(payload)
        self.assertEqual(register_archive_repair_snapshot(payload), registered)
        files = list((self.directory / "repair-snapshots").iterdir())
        self.assertEqual([file.name for file in files], [registered["snapshot_name"]])
        self.assertEqual(files[0].read_bytes(), payload)
        self.assertEqual(self.faces()[0], (1, 1, "active"))
        self.assertEqual(preview_archive_repair()["candidates"][0]["photo_filename"], "new name.jpg")

    def test_cannot_register_foreign_malformed_or_incomplete_export(self):
        other = self.snapshot("other.sqlite", folder="outside-managed-snapshots")
        with closing(sqlite3.connect(other)) as backup, backup:
            backup.execute(
                "UPDATE app_settings SET value = 'other-lineage' WHERE key = 'archive_repair_library_lineage'"
            )
        with self.assertRaisesRegex(ValueError, "nicht nachweisbar"):
            register_archive_repair_snapshot(other.read_bytes())
        with self.assertRaisesRegex(ValueError, "keine gültige"):
            register_archive_repair_snapshot(b"not SQLite")
        with self.assertRaisesRegex(ValueError, "keine gültige"):
            register_archive_repair_snapshot(other.read_bytes()[:4096])
        with closing(sqlite3.connect(other)) as backup, backup:
            backup.execute("DROP TABLE face")
        with self.assertRaisesRegex(ValueError, "keine gültige"):
            register_archive_repair_snapshot(other.read_bytes())
        self.assertEqual(list((self.directory / "repair-snapshots").iterdir()), [])
