from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
from threading import Barrier
import unittest
from unittest.mock import patch

from backend.services import shared_sync_backup
from backend.services.shared_sync_backup import ensure_initial_share_backup


SCOPE = "a7" * 32
DEVICE = "b8" * 16


class SharedSyncBackupTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.database = self.root / "library.sqlite"
        self.directory = self.root / "pre-share-backups"
        self.source = sqlite3.connect(self.database)
        self.addCleanup(self.source.close)
        self.source.execute("PRAGMA journal_mode=WAL")
        self.source.execute("PRAGMA wal_autocheckpoint=0")
        for table in ("image", "face", "cluster", "person"):
            self.source.execute(f"CREATE TABLE {table} (name TEXT)")
        self.source.commit()

    def _save(self, connection=None, *, scope=SCOPE, device=DEVICE, directory=None):
        return ensure_initial_share_backup(
            connection if connection is not None else self.source, directory or self.directory,
            library_scope=scope, producer_id=device,
        )

    def _add_person(self, value):
        self.source.execute("INSERT INTO person VALUES (?)", (value,))
        self.source.commit()

    def _saved_people(self, path):
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as copy:
            self.assertEqual(copy.execute("PRAGMA journal_mode").fetchone(), ("delete",))
            return copy.execute("SELECT name FROM person ORDER BY rowid").fetchall()

    def test_backup_includes_open_wal_and_is_reused_without_replacing(self):
        self._add_person("Ältere Zuordnung")
        self.assertTrue(self.database.with_name("library.sqlite-wal").is_file())
        path = self._save()
        self.assertEqual(self._saved_people(path), [("Ältere Zuordnung",)])
        before = path.read_bytes()
        self._add_person("Spätere Zuordnung")
        self.assertEqual(self._save(), path)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(self.directory.iterdir()), [path])

    def test_each_scope_and_sender_gets_its_own_copy(self):
        self._add_person("Original")
        first = self._save()
        second = self._save(scope="9" * 64)
        third = self._save(device="8" * 32)
        self.assertEqual(len({first, second, third}), 3)
        self.assertTrue(all(path.is_file() and path.parent == self.directory for path in (first, second, third)))

    def test_empty_file_backed_library_leaves_no_scratch(self):
        self.assertIsNone(self._save())
        self.assertFalse(self.directory.exists())

    def test_invalid_scope_external_path_and_active_transaction_fail_closed(self):
        self._add_person("Privat")
        with self.assertRaises(ValueError):
            self._save(scope="../outside")
        with self.assertRaises(ValueError):
            self._save(device="b" * 31)
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                self._save(directory=Path(folder) / "untouched")
            self.assertEqual(list(Path(folder).iterdir()), [])
        self.source.execute("BEGIN")
        with self.assertRaises(ValueError):
            self._save()
        self.assertTrue(self.source.in_transaction)
        self.assertFalse(self.directory.exists())

    @unittest.skipUnless(sys.platform == "linux", "Linux mount detection")
    def test_known_network_filesystem_cannot_be_used_as_data_root(self):
        mounts = self.root / "mountinfo"
        mounts.write_text(
            f"1 0 0:0 / / rw - ext4 /dev/root rw\n2 1 0:1 / {self.root} rw - cifs //nas/library rw\n"
        )
        self._add_person("Privat")
        with patch.object(shared_sync_backup, "_MOUNT_INFO", mounts), self.assertRaises(ValueError):
            self._save()
        self.assertFalse(self.directory.exists())

    def test_new_backup_syncs_both_directory_entries(self):
        self._add_person("Original")
        with patch.object(shared_sync_backup, "_sync_directory", wraps=shared_sync_backup._sync_directory) as sync:
            self._save()
        self.assertEqual([call.args[0] for call in sync.call_args_list], [self.root, self.directory])

    def test_corrupt_existing_file_is_not_overwritten(self):
        self._add_person("Original")
        path = self._save()
        path.write_bytes(b"damaged backup")
        with self.assertRaises(sqlite3.DatabaseError):
            self._save()
        self.assertEqual(path.read_bytes(), b"damaged backup")
        self.assertEqual(list(self.directory.iterdir()), [path])

    def test_deadline_leaves_no_partial_files(self):
        self._add_person("Original")
        with patch.object(shared_sync_backup, "_BACKUP_TIMEOUT_SECONDS", 0), self.assertRaises(TimeoutError):
            self._save()
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_two_callers_create_only_one_valid_published_backup(self):
        self._add_person("Original")
        start = Barrier(2)

        def save():
            with closing(sqlite3.connect(self.database)) as connection:
                start.wait(5)
                return self._save(connection)

        with ThreadPoolExecutor(2) as workers:
            first = workers.submit(save)
            second = workers.submit(save)
            path = first.result(timeout=10)
            self.assertEqual(second.result(timeout=10), path)
        self.assertEqual(self._saved_people(path), [("Original",)])
        self.assertEqual(list(self.directory.iterdir()), [path])
