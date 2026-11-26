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
from backend.services import albums


class AlbumsTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        database = patch.object(schema, "DB_PATH", str(self.root / "library.sqlite"))
        database.start()
        self.addCleanup(database.stop)
        schema.init_db(scan_sources=False)

    def _photos(self, rows):
        with closing(schema.get_conn()) as conn:
            for image_id, latitude, longitude, captured_at in rows:
                name = f"{image_id}.jpg"
                path = str(self.root / name)
                conn.execute(
                    """INSERT INTO image(id,path,directory,filename,latitude,longitude,
                                         captured_at,metadata_scanned_at,processed_at)
                       VALUES (?,?,?,?,?,?,?,'checked','imported')""",
                    (image_id, path, str(self.root), name, latitude, longitude, captured_at),
                )
                conn.execute(
                    "INSERT INTO image_location(image_id,path,directory,filename) VALUES (?,?,?,?)",
                    (image_id, path, str(self.root), name),
                )
            conn.commit()

    def test_membership_and_cover_are_idempotent_and_album_deletion_keeps_photos(self):
        self._photos([(1, 0, 0, None), (2, 0, 0, None), (3, 0, 0, None)])
        with patch.object(app.event_hub, "publish") as event:
            album = app.create_album(app._AlbumCreateBody(name="  Urlaub  ", image_ids=[3, 1, 3]))
            event.assert_called_once_with("clusters", {"reason": "albums_changed"})
        album_id = album["id"]
        self.assertEqual((album["name"], album["photo_count"], album["cover_image_id"]), ("Urlaub", 2, 3))
        self.assertEqual(app.get_album(album_id), album)
        self.assertEqual(app.add_album_photos(album_id, app._AlbumPhotoSelectionBody(image_ids=[1, 3])), album)
        album = app.add_album_photos(album_id, app._AlbumPhotoSelectionBody(image_ids=[2, 1]))
        self.assertEqual(album["photo_count"], 3)
        album = app.update_album(album_id, app._AlbumUpdateBody(name="Sommer", cover_image_id=1))
        self.assertEqual((album["name"], album["cover_image_id"]), ("Sommer", 1))
        album = app.remove_album_photos(album_id, app._AlbumRemovePhotosBody(image_ids=[1, 1]))
        self.assertEqual((album["photo_count"], album["cover_image_id"]), (2, 3))
        self.assertEqual(app.delete_album(album_id), {"status": "deleted"})
        self.assertEqual(app.get_albums(), {"albums": []})
        with closing(schema.get_conn()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM image").fetchone()[0], 3)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM album_photo").fetchone()[0], 0)

    def test_invalid_sources_and_stale_ids_are_atomic(self):
        self._photos([(1, 0, 0, None), (2, 0, 0, None)])
        with self.assertRaises(HTTPException) as stale:
            app.create_album(app._AlbumCreateBody(name="Alt", image_ids=[1, 99]))
        self.assertEqual(stale.exception.status_code, 409)
        self.assertEqual(app.get_albums()["albums"], [])
        album = app.create_album(app._AlbumCreateBody(name="Neu", image_ids=[1]))
        album_id = album["id"]
        with self.assertRaises(HTTPException) as no_member:
            app.update_album(album_id, app._AlbumUpdateBody(cover_image_id=2))
        self.assertEqual(no_member.exception.status_code, 422)
        with self.assertRaises(HTTPException) as empty_selection:
            app.add_album_photos(album_id, app._AlbumPhotoSelectionBody(selection={}))
        self.assertEqual(empty_selection.exception.status_code, 422)
        with self.assertRaises(HTTPException) as changing:
            with patch.object(app, "_database_restore_in_progress", True):
                app.add_album_photos(album_id, app._AlbumPhotoSelectionBody(image_ids=[2]))
        self.assertEqual(changing.exception.status_code, 409)
        self.assertEqual(app.get_album(album_id), album)

    def test_date_area_and_album_scopes_share_sql_without_a_page_limit(self):
        day = date(2024, 1, 1)
        self._photos([
            *((index, 1, 179, "2024-01-01T12:00:00") for index in range(1, 242)),
            (242, 1, -179, "2024-01-01T12:00:00"), (243, 1, 179, "2024-01-02T12:00:00"),
            (244, 1, 0, "2024-01-01T12:00:00"), (245, None, None, "2024-01-01T12:00:00"),
        ])
        area = {"west": 170, "east": -170, "south": 0, "north": 2}
        album = app.create_album(app._AlbumCreateBody(
            name="Datumsgrenze", selection={"bounds": area, "from_date": day, "to_date": day},
        ))
        album_id = album["id"]
        self.assertEqual(album["photo_count"], 242)
        request = SimpleNamespace(headers={})
        library = app.get_images(
            request, folders=[], persons=[], face_statuses=[], all_photos=True, limit=3, offset=0,
            album_id=album_id, **area,
        )
        self.assertEqual((library["total"], len(library["items"])), (242, 3))
        mapped = app.get_map_images(
            album_id=album_id, limit=1, offset=0, before_id=None, before_captured_at=None, **area,
        )
        self.assertEqual(mapped["total"], 242)
        self.assertEqual(mapped["items"][0]["id"], 242)
        points = app.get_map_points(
            album_id=album_id, zoom=2, grid_columns=8, grid_rows=8, **area,
        )
        self.assertEqual((points["library_total"], points["located_total"], points["total"]), (242, 242, 242))
        journey = app.get_map_journey(album_id=album_id, from_date=day, to_date=day, **area)
        self.assertEqual({photo_id for stop in journey["stops"] for photo_id in stop["image_ids"]}, set(range(1, 243)))
        exact = app.get_selected_map_images(app._GeoSelectionBody(album_id=album_id, image_ids=[1, 243, 244]))
        self.assertEqual([item["id"] for item in exact["items"]], [1])
        other = app.create_album(app._AlbumCreateBody(name="Gefilterte Kopie"))
        copied = app.add_album_photos(other["id"], app._AlbumPhotoSelectionBody(
            selection={"album_id": album_id, "image_ids": [242, 243, 244]},
        ))
        self.assertEqual((copied["photo_count"], copied["cover_image_id"]), (1, 242))

    def test_merge_and_cascade_preserve_canonical_membership_and_cover(self):
        self._photos([(1, 0, 0, None), (2, 0, 0, None), (3, 0, 0, None)])
        first = albums.create_album("Erstes", image_ids=[2, 3, 1])
        second = albums.create_album("Zweites", image_ids=[2])
        albums.update_album(first["id"], {"cover_image_id": 2})
        with closing(schema.get_conn()) as conn:
            conn.execute("DROP INDEX idx_image_content_hash")
            conn.execute("UPDATE image SET content_hash = ? WHERE id IN (1, 2)", ("a" * 64,))
            conn.commit()
        schema.init_db(scan_sources=False)
        self.assertEqual(albums.get_album(first["id"])["cover_image_id"], 1)
        self.assertEqual(albums.get_album(second["id"])["photo_count"], 1)
        with closing(schema.get_conn()) as conn:
            self.assertEqual([row["image_id"] for row in conn.execute(
                "SELECT image_id FROM album_photo WHERE album_id = ? ORDER BY position", (first["id"],),
            )], [1, 3])
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            conn.execute("DELETE FROM image WHERE id = 1")
            conn.commit()
        self.assertEqual(albums.get_album(first["id"])["cover_image_id"], 3)
        self.assertIsNone(albums.get_album(second["id"])["cover_image_id"])

    def test_journey_and_validated_draft_round_trip(self):
        album = app.create_album(app._AlbumCreateBody(
            name="Tour", journey={"detail": "city", "from_date": "2024-01-01", "title": " Reise "},
        ))
        self.assertEqual(album["journey"], {"detail": "city", "from_date": "2024-01-01", "title": "Reise"})
        self.assertIsNone(album["layout_draft"])
        draft = {"version": 1, "title": "Route", "palette": "coast", "style": "satellite",
                 "formatting": {"typeface": "sans"}, "transports": {"1:2": "train"}, "showStopsList": False}
        result = app.update_album(album["id"], app._AlbumUpdateBody(layout_draft=draft))
        self.assertEqual(result["layout_draft"], draft)
        with self.assertRaises(ValidationError):
            app._AlbumUpdateBody(layout_draft={"version": 1, "unknown": "/path"})
        large = {"version": 1, "transports": {f"{index:019}:{index:019}": "train" for index in range(800)}}
        with self.assertRaises(HTTPException) as too_big:
            app.update_album(album["id"], app._AlbumUpdateBody(layout_draft=large))
        self.assertEqual(too_big.exception.status_code, 422)
        self.assertEqual(app.get_album(album["id"])["layout_draft"], draft)
        cleared = app.update_album(album["id"], app._AlbumUpdateBody(layout_draft=None, journey=None))
        self.assertIsNone(cleared["layout_draft"])
        self.assertIsNone(cleared["journey"])

    def test_safety_backup_retains_album_reference_and_layout_draft(self):
        self._photos([(1, 0, 0, "2024-01-01T12:00:00")])
        album = app.create_album(app._AlbumCreateBody(
            name="Urlaub", image_ids=[1], journey={"detail": "trip"},
            layout_draft={"version": 1, "title": "Am Meer", "style": "satellite"},
        ))
        backup = self.root / "backup.sqlite"
        app.snapshot_database_backup(backup)
        app.validate_database_file(backup)
        with patch.object(schema, "DB_PATH", str(backup)):
            schema.init_db(scan_sources=False)
            self.assertEqual(app.get_album(album["id"]), album)
            with closing(schema.get_conn()) as conn:
                self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])


if __name__ == "__main__":
    unittest.main()
