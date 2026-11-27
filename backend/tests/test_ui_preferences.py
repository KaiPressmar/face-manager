import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

from fastapi import HTTPException, Response

from backend import app
from backend.services import ui_preferences


class UiPreferencesTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        data_root = patch.object(ui_preferences, "get_data_root", return_value=self.root)
        data_root.start()
        self.addCleanup(data_root.stop)

    def read(self):
        response = Response()
        values = app.api_get_ui_preferences(response)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        return values

    def update(self, values, **options):
        return app.api_update_ui_preferences({"values": values, **options})

    def test_sparse_merge_and_legacy_import(self):
        self.assertEqual(self.read(), {})
        self.assertEqual(self.update({"map_style": "satellite", "map_country_names": False}), {
            "map_style": "satellite", "map_country_names": False,
        })
        imported = self.update({
            "map_style": "landscape", "map_country_names": True,
            "image_grid_size": "large", "help_introduction_seen": True,
        }, only_if_absent=True)
        self.assertEqual(imported, {
            "map_style": "satellite", "map_country_names": False,
            "image_grid_size": "large", "help_introduction_seen": True,
        })
        self.assertEqual(self.update({"map_style": "standard"}), {**imported, "map_style": "standard"})
        self.assertEqual(self.read(), {**imported, "map_style": "standard"})
        self.assertTrue((self.root / "ui-preferences.sqlite").is_file())

    def test_invalid_patch_does_not_write_valid_fields(self):
        for bad_values in (
            {"map_style": "street"}, {"image_grid_size": "tiny"}, {"map_country_names": "true"},
            {"map_country_names": 1}, {"map_country_names": None}, {"help_introduction_seen": []},
            {"ui_theme": "dark"},
        ):
            with self.subTest(bad_values=bad_values):
                with self.assertRaises(HTTPException) as raised:
                    self.update({"map_style": "standard", "help_introduction_seen": True, **bad_values})
                self.assertEqual(raised.exception.status_code, 422)
        for payload in (
            [], {}, {"values": None}, {"values": [["map_country_names", True]]},
            {"values": {"map_country_names": True}, "only_if_absent": "false"},
            {"values": {"map_country_names": True}, "unexpected": False},
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(HTTPException) as raised:
                    app.api_update_ui_preferences(payload)
                self.assertEqual(raised.exception.status_code, 422)
        self.assertEqual(self.read(), {})

    def test_parallel_disjoint_writes_and_legacy_import_preserve_the_latest_choice(self):
        start = Barrier(3)

        def update(values, **options):
            start.wait(timeout=5)
            return self.update(values, **options)

        with ThreadPoolExecutor(max_workers=3) as workers:
            futures = [
                workers.submit(update, {"map_style": "satellite"}),
                workers.submit(update, {"image_grid_size": "large"}),
                workers.submit(update, {"map_style": "landscape", "map_country_names": True}, only_if_absent=True),
            ]
            for future in futures:
                future.result(timeout=5)
        self.assertEqual(self.read(), {
            "map_style": "satellite", "map_country_names": True, "image_grid_size": "large",
        })

    def test_locked_database_reports_a_retryable_error(self):
        self.update({"map_style": "satellite"})
        with closing(sqlite3.connect(self.root / "ui-preferences.sqlite")) as locked, locked:
            locked.execute("BEGIN EXCLUSIVE")
            with self.assertLogs(app.logger, level="WARNING"), self.assertRaises(HTTPException) as raised:
                self.update({"map_style": "landscape"})
            self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(self.read(), {"map_style": "satellite"})
