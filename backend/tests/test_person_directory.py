import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from backend import app
from backend.db import schema
from backend.services import storage


class PersonDirectoryTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        database = patch.object(schema, "DB_PATH", str(Path(folder.name) / "library.sqlite"))
        database.start()
        self.addCleanup(database.stop)
        storage.app_cache.clear()
        self.addCleanup(storage.app_cache.clear)
        schema.init_db()

        with closing(schema.get_conn()) as conn, conn:
            conn.executemany("INSERT INTO person(id, name) VALUES (?, ?)", [
                (10, "Ben"), (20, "Zoë %"), (30, "Anna"), (40, "Änne"),
            ])
            conn.executemany("INSERT INTO cluster(id, person_id) VALUES (?, ?)", [
                (101, 10), (102, 10), (201, 20),
            ])
            for image_id in range(1, 6):
                filename = f"photo-{image_id}.jpg"
                path = f"/pictures/{filename}"
                values = (image_id, path, "/pictures", filename)
                conn.execute("INSERT INTO image(id, path, directory, filename) VALUES (?, ?, ?, ?)", values)
                if image_id != 5:
                    conn.execute("INSERT INTO image_location(image_id, path, directory, filename) VALUES (?, ?, ?, ?)", values)
            conn.execute("INSERT INTO image_location(image_id, path, directory, filename) VALUES (1, '/copies/first.jpg', '/copies', 'first.jpg')")
            conn.executemany("INSERT INTO face(id, image_id, cluster_id, review_status) VALUES (?, ?, ?, ?)", [
                (1, 3, 101, "unknown_person"),
                (2, 1, 101, "active"),
                (3, 1, 102, "active"),
                (4, 2, 102, "active"),
                (5, 3, 101, "not_face"),
                (6, 4, 201, "active"),
                (7, 5, 102, "active"),
            ])

    def test_search_pagination_and_representative_faces(self):
        first = app.api_person_directory(limit=2)
        second = app.api_person_directory(limit=2, offset=2)
        self.assertEqual((first["total"], first["offset"], first["limit"], first["has_more"]), (4, 0, 2, True))
        self.assertEqual([person["id"] for person in first["items"]], [30, 40])
        self.assertEqual([person["id"] for person in second["items"]], [10, 20])
        self.assertFalse(second["has_more"])
        self.assertEqual(first["items"][0], {
            "id": 30, "name": "Anna", "representative_face_id": None, "photo_count": 0,
        })
        self.assertEqual(app.api_person_profile(10), {
            **second["items"][0], "assignment_library_id": app.get_person_suggestion_epoch(),
        })
        self.assertEqual(second["items"][0], {
            "id": 10, "name": "Ben", "representative_face_id": 2, "photo_count": 2,
        })
        self.assertEqual([item["id"] for item in app.api_person_directory(search=" ÄN ")["items"]], [30, 40])
        self.assertEqual([item["id"] for item in app.api_person_directory(search="%")["items"]], [20])
        self.assertEqual(app.api_person_directory(offset=20)["items"], [])

    def test_photos_are_distinct_and_profile_is_scoped_by_id(self):
        request = SimpleNamespace(headers={})
        first = app.api_person_photos(request, 10, limit=1)
        second = app.api_person_photos(request, 10, limit=1, offset=1)
        self.assertEqual({first["items"][0]["id"], second["items"][0]["id"]}, {1, 2})
        self.assertEqual((first["total"], second["total"]), (2, 2))
        self.assertEqual((first["has_more"], second["has_more"]), (True, False))
        self.assertEqual(app.api_person_photos(request, 30)["items"], [])
        with self.assertRaises(HTTPException) as missing:
            app.api_person_profile(999)
        self.assertEqual(missing.exception.status_code, 404)
        with self.assertRaises(HTTPException) as missing_photos:
            app.api_person_photos(request, 999)
        self.assertEqual(missing_photos.exception.status_code, 404)

    def test_cluster_summary_uses_a_representative_active_face(self):
        list_item = next(item for item in storage.list_cluster_summaries() if item["cluster_id"] == 101)
        self.assertEqual((list_item["face_count"], list_item["representative_face_id"]), (1, 2))
        self.assertEqual(storage.get_cluster_summary(101), list_item)

    def test_guarded_rename_and_merge_invalidate_directory_and_keep_exact_scope(self):
        request = SimpleNamespace(headers={})
        self.assertEqual(app.api_person_profile(10)["photo_count"], 2)
        self.assertEqual(app.api_person_photos(request, 20)["total"], 1)
        self.assertEqual(app.api_person_directory(search="Ben")["total"], 1)

        with patch.object(app, "notify_clusters_changed"), patch.object(app.person_suggestion_worker, "wake"):
            for payload in (
                {"name": "Zoë %", "expected_target_person_id": None},
                {"name": "Zoë %", "expected_target_person_id": 30},
                {"name": "Ein freier Name", "expected_target_person_id": 20},
            ):
                with self.assertRaises(HTTPException) as stale:
                    app.api_rename_person(10, payload)
                self.assertEqual(stale.exception.status_code, 409)
                self.assertEqual(app.api_person_profile(10)["name"], "Ben")

            renamed = app.api_rename_person(10, {
                "name": "Benn", "expected_target_person_id": None,
                "expected_library_id": app.get_person_suggestion_epoch(),
            })
            self.assertEqual(renamed["person_id"], 10)
            self.assertEqual(app.api_person_profile(10)["name"], "Benn")
            self.assertEqual(app.api_person_directory(search="Benn")["items"][0]["id"], 10)
            merged = app.api_rename_person(10, {"name": "ZOË %", "expected_target_person_id": 20})
            self.assertEqual(merged["person_id"], 20)
            self.assertEqual(app.api_person_photos(request, 20)["total"], 3)
            with self.assertRaises(HTTPException) as merged_source:
                app.api_person_profile(10)
            self.assertEqual(merged_source.exception.status_code, 404)

    def test_person_management_rejects_a_replaced_library_even_when_ids_match(self):
        previous_library = app.api_person_profile(10)["assignment_library_id"]
        with patch.object(app, "get_person_suggestion_epoch", return_value="replacement-library"):
            with self.assertRaises(HTTPException) as stale_rename:
                app.api_rename_person(10, {
                    "name": "Zoë %", "expected_target_person_id": 20, "expected_library_id": previous_library,
                })
            self.assertEqual(stale_rename.exception.status_code, 409)
            with self.assertRaises(HTTPException) as stale_delete:
                app.api_delete_person(10, "unassigned", expected_library_id=previous_library)
            self.assertEqual(stale_delete.exception.status_code, 409)
        self.assertEqual(app.api_person_profile(10)["name"], "Ben")
