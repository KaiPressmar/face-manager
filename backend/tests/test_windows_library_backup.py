import runpy
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


class WindowsLibraryBackupTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[2]
        module = runpy.run_path(str(root / "packaging/windows/backup-library.py"))
        self.backup_library = module["backup_library"]

    def test_backup_contains_open_wal_and_can_be_opened_without_sidecars(self):
        with tempfile.TemporaryDirectory(prefix="library & photos ") as directory:
            database = Path(directory) / "database.sqlite"
            with closing(sqlite3.connect(database)) as writer:
                writer.execute("PRAGMA journal_mode = WAL")
                writer.execute("PRAGMA wal_autocheckpoint = 0")
                writer.execute("CREATE TABLE person (name TEXT)")
                writer.execute("INSERT INTO person VALUES ('Ältere Zuordnung')")
                writer.commit()
                self.assertGreater(database.with_name("database.sqlite-wal").stat().st_size, 0)

                self.backup_library(database, "0.13.0")

            backups = list((database.parent / "update-backups").iterdir())
            self.assertEqual(len(backups), 1)
            self.assertRegex(backups[0].name, r"^database-0\.13\.0-.*\.sqlite$")
            with closing(sqlite3.connect(backups[0].as_uri() + "?mode=ro", uri=True)) as backup:
                self.assertEqual(backup.execute("PRAGMA journal_mode").fetchone(), ("delete",))
                self.assertEqual(backup.execute("SELECT name FROM person").fetchall(), [("Ältere Zuordnung",)])

    def test_invalid_database_leaves_no_partial_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "database.sqlite"
            database.write_bytes(b"invalid SQLite database")

            with self.assertRaises(sqlite3.DatabaseError):
                self.backup_library(database)

            self.assertEqual(list((database.parent / "update-backups").iterdir()), [])
