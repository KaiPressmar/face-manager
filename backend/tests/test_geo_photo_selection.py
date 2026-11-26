import tempfile
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError

from backend import app
from backend.db import schema
from backend.services.storage import app_cache


class GeoPhotoSelectionTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name) / "photos"
        db = Path(temp.name) / "selection.sqlite"
        database = patch.object(schema, "DB_PATH", str(db))
        database.start()
        self.addCleanup(database.stop)
        app_cache.clear()
        self.addCleanup(app_cache.clear)
        schema.init_db()
        self.request = SimpleNamespace(headers={})
        with closing(schema.get_conn()) as conn, conn:
            conn.executemany("INSERT INTO person(id, name) VALUES (?, ?)", [(1, "Anna"), (2, "Ben")])
            conn.executemany("INSERT INTO cluster(id, person_id) VALUES (?, ?)", [(1, 1), (2, 2)])
            for image_id, longitude, captured, cluster, review_status in [
                (1, 179, "2026-05-04T23:59:59", 1, "active"),
                (2, -179, "2026-05-04T23:59:59", 2, "active"),
                (3, 170, "2026-05-04T10:00:00", 1, "active"),
                (4, 179, "2026-05-05T01:00:00", 1, "active"),
                (5, None, "2026-05-04T10:00:00", 2, "active"),
                (6, 179, None, 1, "active"),
                (7, 179, "2026-05-04T12:00:00", None, "not_face"),
            ]:
                folder = self.folder / ("west" if image_id == 2 else "east")
                filename = f"photo-{image_id}.jpg"
                path = folder / filename
                conn.execute(
                    """INSERT INTO image(id, path, directory, filename, latitude, longitude, captured_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (image_id, str(path), str(folder), filename, 10 if longitude is not None else None, longitude, captured),
                )
                conn.execute("INSERT INTO image_location(image_id, path, directory, filename) VALUES (?, ?, ?, ?)",
                             (image_id, str(path), str(folder), filename))
                conn.execute(
                    """INSERT INTO face(image_id, cluster_id, review_status, bbox_x, bbox_y, bbox_w, bbox_h)
                    VALUES (?, ?, ?, 0, 0, 1, 1)""", (image_id, cluster, review_status),
                )

    def list_images(self, **kwargs):
        filters = dict(folders=[], persons=[], face_statuses=[], limit=40, offset=0, all_photos=True)
        filters.update(kwargs)
        return app.get_images(self.request, **filters)

    def test_date_line_bounds_and_inclusive_dates_intersect_existing_filters(self):
        geo = dict(west=178, east=-178, south=9, north=11, from_date=date(2026, 5, 4), to_date=date(2026, 5, 4))
        self.assertEqual({item["id"] for item in self.list_images(**geo)["items"]}, {1, 2, 7})
        self.assertEqual(self.list_images(**geo, persons=["Anna"], folders=[str(self.folder / "west")])["total"], 0)
        self.assertEqual([item["id"] for item in self.list_images(**geo, persons=["Ben"])["items"]], [2])
        archived = self.list_images(**geo, face_statuses=["not_face"])["items"]
        self.assertEqual([item["id"] for item in archived], [7])
        self.assertEqual(archived[0]["faces"][0]["review_status"], "not_face")

    def test_date_only_keeps_dated_photos_without_gps(self):
        page = self.list_images(from_date=date(2026, 5, 4), to_date=date(2026, 5, 4))
        self.assertEqual({item["id"] for item in page["items"]}, {1, 2, 3, 5, 7})

    def test_exact_sets_use_one_id_bind_and_keep_map_cursor(self):
        selection = app._GeoSelectionBody(image_ids=[*range(10, 2510), 1, 2, 4, 5, 4])
        page = app.search_selected_images(self.request, selection, folders=[], persons=[], face_statuses=[], all_photos=True)
        self.assertEqual({item["id"] for item in page["items"]}, {1, 2, 4, 5})
        first = app.get_selected_map_images(selection, limit=2)
        second = app.get_selected_map_images(selection, limit=2, **first["next_cursor"])
        self.assertEqual([item["id"] for item in first["items"]], [4, 2])
        self.assertEqual([item["id"] for item in second["items"]], [1])
        self.assertEqual(first["total"], second["total"], 3)

        empty = app._GeoSelectionBody(image_ids=[])
        self.assertEqual(app.search_selected_images(
            self.request, empty, folders=[], persons=[], face_statuses=[], all_photos=True,
        )["total"], 0)

    def test_invalid_bounds_dates_and_exact_ids_fail_closed(self):
        for kwargs in (
            dict(west=-180, east=180),
            dict(west=-180, east=180, south=11, north=9),
            dict(from_date=date(2026, 5, 5), to_date=date(2026, 5, 4)),
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(HTTPException) as raised:
                self.list_images(**kwargs)
            self.assertEqual(raised.exception.status_code, 422)
        for value in (0, -1, True, "1", 2**63):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                app._GeoSelectionBody(image_ids=[value])
