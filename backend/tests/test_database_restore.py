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

    def test_failed_install_restores_old_database_without_file_metadata_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            current = Path(folder) / "database.sqlite"
            incoming = Path(folder) / "incoming.sqlite"
            make_database(current, "old")
            make_database(incoming, "new")
            payload = incoming.read_bytes()

            with ExitStack() as patches:
                patches.enter_context(patch.object(app, "DB_PATH", str(current)))
                patches.enter_context(patch.object(app, "get_conn", side_effect=lambda: sqlite3.connect(current)))
                patches.enter_context(patch.object(app.import_queue, "snapshot", return_value={"running_count": 0, "queued_count": 0, "paused_count": 0}))
                patches.enter_context(patch.object(app.auto_cluster_queue, "request_cancel", return_value=True))
                patches.enter_context(patch.object(app.geo_backfill, "stop"))
                patches.enter_context(patch.object(app.geo_backfill, "start"))
                patches.enter_context(patch.object(app, "init_db", side_effect=[RuntimeError("migration failed"), None]))
                patches.enter_context(patch.object(app, "schedule_version_clustering_upgrade", return_value=True))
                patches.enter_context(patch.object(app, "reset_import_resources"))
                patches.enter_context(patch.object(app.app_cache, "clear"))
                with self.assertRaises(HTTPException) as raised:
                    app.api_import_database(payload)

            self.assertEqual(raised.exception.status_code, 500)
            self.assertEqual(marker_at(current), "old")
            self.assertFalse(current.with_name("database.pre-import-backup.sqlite").exists())
