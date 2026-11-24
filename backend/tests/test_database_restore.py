"""Database restore keeps a consistent backup without copying Android file labels."""

from contextlib import ExitStack, closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from backend import app


def make_database(path: Path, marker: str) -> None:
    with closing(sqlite3.connect(path)) as connection:
        for table in ("image", "face", "cluster", "person"):
            connection.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")
        connection.execute("CREATE TABLE marker (value TEXT)")
        connection.execute("INSERT INTO marker VALUES (?)", (marker,))
        connection.commit()


def marker_at(path: Path) -> str:
    with closing(sqlite3.connect(path)) as connection:
        return connection.execute("SELECT value FROM marker").fetchone()[0]


class DatabaseRestoreTest(unittest.TestCase):
    def test_backup_includes_uncheckpointed_wal_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            source_path = Path(folder) / "source.sqlite"
            backup_path = Path(folder) / "backup.sqlite"
            with closing(sqlite3.connect(source_path)) as writer:
                writer.execute("PRAGMA journal_mode=WAL")
                writer.execute("CREATE TABLE marker (value TEXT)")
                writer.execute("INSERT INTO marker VALUES ('from-wal')")
                writer.commit()
                with patch.object(app, "get_conn", side_effect=lambda: sqlite3.connect(source_path)):
                    app.snapshot_database_backup(backup_path)
                self.assertEqual(marker_at(backup_path), "from-wal")

    def _isolate_restore(self, patches, current):
        patches.enter_context(patch.object(app, "DB_PATH", str(current)))
        patches.enter_context(patch.object(app, "get_conn", side_effect=lambda: sqlite3.connect(current)))
        patches.enter_context(patch.object(app, "ensure_database_is_idle"))
        patches.enter_context(patch.object(app.auto_cluster_queue, "request_cancel", return_value=True))
        patches.enter_context(patch.object(app.auto_cluster_queue, "snapshot", return_value={"task": None}))
        patches.enter_context(patch.object(app.auto_cluster_queue, "notify_ready"))
        patches.enter_context(patch.object(app.image_path_cleanup, "stop", return_value=True))
        patches.enter_context(patch.object(app.image_path_cleanup, "resume"))
        patches.enter_context(patch.object(app.geo_backfill, "stop"))
        patches.enter_context(patch.object(app.geo_backfill, "start"))
        patches.enter_context(patch.object(app, "schedule_version_clustering_upgrade", return_value=True))
        patches.enter_context(patch.object(app, "reset_import_resources"))
        patches.enter_context(patch.object(app.app_cache, "clear"))

    def test_failed_install_restores_old_database_without_copying_file_metadata(self):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as patches:
            current = Path(folder) / "database.sqlite"
            incoming = Path(folder) / "incoming.sqlite"
            make_database(current, "old")
            make_database(incoming, "new")
            self._isolate_restore(patches, current)
            patches.enter_context(patch.object(app, "init_db", side_effect=[RuntimeError("migration failed"), None]))

            with self.assertRaises(HTTPException) as raised:
                app.api_import_database(incoming.read_bytes())

            self.assertEqual(raised.exception.status_code, 500)
            self.assertEqual(marker_at(current), "old")
            self.assertFalse(list(current.parent.glob("*.pre-import-backup-*.sqlite")))
            self.assertTrue(all(call.kwargs == {"recover": False} for call in app.init_db.call_args_list))

    def test_incomplete_snapshot_keeps_live_database_and_older_recovery_copy(self):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as patches:
            current = Path(folder) / "database.sqlite"
            incoming = Path(folder) / "incoming.sqlite"
            earlier_backup = Path(folder) / "database.pre-import-backup-earlier.sqlite"
            make_database(current, "old")
            make_database(incoming, "new")
            make_database(earlier_backup, "earlier")
            self._isolate_restore(patches, current)

            def fail_snapshot(path):
                path.write_bytes(b"partial")
                raise OSError("disk full")

            patches.enter_context(patch.object(app, "snapshot_database_backup", side_effect=fail_snapshot))
            with self.assertRaises(HTTPException) as raised:
                app.api_import_database(incoming.read_bytes())

            self.assertEqual(raised.exception.status_code, 500)
            self.assertEqual(marker_at(current), "old")
            self.assertEqual(marker_at(earlier_backup), "earlier")
            self.assertEqual(list(current.parent.glob("*.pre-import-backup-*.sqlite")), [earlier_backup])

    def test_failed_rollback_keeps_independent_valid_backup(self):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as patches:
            current = Path(folder) / "database.sqlite"
            incoming = Path(folder) / "incoming.sqlite"
            make_database(current, "old")
            make_database(incoming, "new")
            self._isolate_restore(patches, current)
            patches.enter_context(patch.object(app, "init_db", side_effect=RuntimeError("migration failed")))
            original_replace = app.os.replace

            def fail_rollback(source, destination):
                if ".rollback-" in Path(source).name:
                    raise PermissionError("file is in use")
                return original_replace(source, destination)

            patches.enter_context(patch.object(app.os, "replace", side_effect=fail_rollback))
            with self.assertRaises(HTTPException) as raised:
                app.api_import_database(incoming.read_bytes())

            self.assertEqual(raised.exception.status_code, 500)
            backup, = current.parent.glob("*.pre-import-backup-*.sqlite")
            self.assertEqual(marker_at(backup), "old")
            self.assertIn(str(backup), raised.exception.detail)
            app.geo_backfill.start.assert_not_called()

    def test_active_path_check_prevents_replacing_database(self):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as patches:
            current = Path(folder) / "database.sqlite"
            incoming = Path(folder) / "incoming.sqlite"
            make_database(current, "old")
            make_database(incoming, "new")
            self._isolate_restore(patches, current)
            app.image_path_cleanup.stop.return_value = False

            with self.assertRaises(HTTPException) as raised:
                app.api_import_database(incoming.read_bytes())

            self.assertEqual(raised.exception.status_code, 409)
            self.assertEqual(marker_at(current), "old")
            self.assertFalse(list(current.parent.glob("*.pre-import-backup-*.sqlite")))
            app.geo_backfill.stop.assert_not_called()
            app.image_path_cleanup.resume.assert_called_once()

    def test_person_suggestion_worker_drains_before_database_swap(self):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as patches:
            current = Path(folder) / "database.sqlite"
            incoming = Path(folder) / "incoming.sqlite"
            make_database(current, "old")
            make_database(incoming, "new")
            self._isolate_restore(patches, current)
            patches.enter_context(patch.object(app, "init_db"))
            worker = patches.enter_context(patch.object(app, "person_suggestion_worker"))
            worker.stop.return_value = True
            original_replace = app.os.replace
            old_epoch = app.get_person_suggestion_epoch()

            def assert_drained_then_replace(source, destination):
                worker.stop.assert_called_once()
                worker.start.assert_not_called()
                self.assertTrue(app._database_restore_in_progress)
                original_replace(source, destination)

            patches.enter_context(patch.object(app.os, "replace", side_effect=assert_drained_then_replace))

            self.assertEqual(app.api_import_database(incoming.read_bytes()), {"status": "imported"})
            self.assertEqual(marker_at(current), "new")
            self.assertNotEqual(app.get_person_suggestion_epoch(), old_epoch)
            worker.start.assert_called_once()

    def test_inflight_clustering_prevents_replacing_database(self):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as patches:
            current = Path(folder) / "database.sqlite"
            incoming = Path(folder) / "incoming.sqlite"
            make_database(current, "old")
            make_database(incoming, "new")
            self._isolate_restore(patches, current)
            app.auto_cluster_queue.snapshot.return_value = {"task": {"status": "cancelling"}}

            with self.assertRaises(HTTPException) as raised:
                app.api_import_database(incoming.read_bytes())

            self.assertEqual(raised.exception.status_code, 409)
            self.assertEqual(marker_at(current), "old")
            app.image_path_cleanup.stop.assert_not_called()
            self.assertFalse(app._database_restore_in_progress)
