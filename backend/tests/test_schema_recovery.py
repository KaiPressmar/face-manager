import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.db import schema


class SchemaRecoveryTest(unittest.TestCase):
    def test_recover_database_moves_broken_db_and_sidecars(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "database.sqlite"
            db_path.write_text("broken", encoding="utf-8")
            wal_path = db_path.with_name("database.sqlite-wal")
            shm_path = db_path.with_name("database.sqlite-shm")
            wal_path.write_text("wal", encoding="utf-8")
            shm_path.write_text("shm", encoding="utf-8")

            error = sqlite3.DatabaseError("database disk image is malformed")
            with patch.object(schema, "DB_PATH", str(db_path)):
                archived_path = schema.recover_database(error, "unit-test")

            self.assertIsNotNone(archived_path)
            self.assertFalse(db_path.exists())
            self.assertTrue(archived_path.exists())
            self.assertTrue(
                archived_path.with_name(f"{archived_path.name}-wal").exists()
            )
            self.assertTrue(
                archived_path.with_name(f"{archived_path.name}-shm").exists()
            )

    def test_init_db_retries_after_recoverable_database_error(self):
        marker_error = sqlite3.DatabaseError("database disk image is malformed")
        healthy_connection = sqlite3.connect(":memory:")
        healthy_connection.row_factory = sqlite3.Row

        with patch.object(schema, "_open_connection", side_effect=[marker_error, healthy_connection]), patch.object(
            schema,
            "recover_database",
            return_value=Path("/tmp/database.corrupt.sqlite"),
        ) as recover_database:
            schema.init_db()

        recover_database.assert_called_once()
        healthy_connection.close()
