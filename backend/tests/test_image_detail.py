import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from PIL import Image

from backend import app
from backend.db import schema
from backend.services import storage


class ImageDetailWithoutFacesTest(unittest.TestCase):
    def test_photo_library_keeps_pictures_without_faces_and_respects_face_filters(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            schema, "DB_PATH", str(Path(temp_dir) / "images.sqlite")
        ):
            storage.app_cache.clear()
            self.addCleanup(storage.app_cache.clear)
            schema.init_db()
            with closing(schema.get_conn()) as conn:
                photos = [(1, "landscape.jpg"), (2, "portrait.jpg"), (3, "archived.jpg"), (4, "older.jpg")]
                for image_id, filename in photos:
                    path = str(Path(temp_dir) / filename)
                    values = (image_id, path, temp_dir, filename)
                    conn.execute("INSERT INTO image(id,path,directory,filename) VALUES (?,?,?,?)", values)
                    conn.execute("INSERT INTO image_location(image_id,path,directory,filename) VALUES (?,?,?,?)", values)
                conn.execute("INSERT INTO face(image_id,bbox_x,bbox_y,bbox_w,bbox_h) VALUES (2,1,2,3,4)")
                conn.execute("INSERT INTO face(image_id,bbox_x,bbox_y,bbox_w,bbox_h,review_status) VALUES (3,1,2,3,4,'not_face')")
                conn.execute("INSERT INTO face(image_id) VALUES (4)")
                conn.commit()

            request = SimpleNamespace(headers={})
            library = app.get_images(request, folders=[], persons=[], limit=40, offset=0, all_photos=True)
            self.assertEqual(library["total"], 4)
            self.assertEqual({item["id"]: len(item["faces"]) for item in library["items"]}, {1: 0, 2: 1, 3: 0, 4: 0})
            archived = app.get_images(
                request, folders=[], persons=[], limit=40, offset=0, all_photos=True,
                face_statuses=["not_face"],
            )
            self.assertEqual([item["id"] for item in archived["items"]], [3])
            self.assertEqual(archived["items"][0]["faces"][0]["review_status"], "not_face")
            portrait = app.api_face_image(request, 1)
            self.assertEqual(portrait["id"], 2)
            self.assertEqual(portrait["faces"][0]["id"], 1)
            with self.assertRaises(HTTPException) as missing_face:
                app.api_face_image(request, 999)
            self.assertEqual(missing_face.exception.status_code, 404)

    def test_image_with_no_faces_keeps_locations_and_empty_faces(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "images.sqlite"
            with patch.object(schema, "DB_PATH", str(db_path)):
                schema.init_db()
                conn = schema.get_conn()
                conn.execute(
                    """INSERT INTO image(id,path,directory,filename,content_hash)
                       VALUES (85,'/photos/original.jpg','/photos','original.jpg','hash')"""
                )
                conn.executemany(
                    """INSERT INTO image_location(image_id,path,directory,filename,created_at)
                       VALUES (85,?, '/photos', ?, '2024-02-03T10:00:00')""",
                    [('/photos/copy.jpg', 'copy.jpg'),
                     ('/photos/original.jpg', 'original.jpg')],
                )
                conn.commit()
                conn.close()
                result = app.api_image_detail(SimpleNamespace(headers={}), 85)

        self.assertEqual(result["id"], 85)
        self.assertEqual(result["image_path"], "/photos/copy.jpg")
        self.assertEqual(result["faces"], [])
        self.assertEqual(result["location_count"], 2)
        self.assertEqual([item["path"] for item in result["locations"]],
                         ["/photos/copy.jpg", "/photos/original.jpg"])

    def test_image_with_only_archived_face_still_has_empty_active_faces(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "images.sqlite"
            with patch.object(schema, "DB_PATH", str(db_path)):
                schema.init_db()
                conn = schema.get_conn()
                conn.execute(
                    "INSERT INTO image(id,path,directory,filename) VALUES (85,'/photos/a.jpg','/photos','a.jpg')"
                )
                conn.execute(
                    """INSERT INTO face(image_id,bbox_x,bbox_y,bbox_w,bbox_h,review_status)
                       VALUES (85,1,2,3,4,'not_face')"""
                )
                conn.commit()
                conn.close()
                result = app.api_image_detail(SimpleNamespace(headers={}), 85)
        self.assertEqual(result["faces"], [])
        self.assertEqual(result["image_path"], "/photos/a.jpg")
        self.assertEqual(result["locations"], [])

    def test_missing_image_remains_not_found(self):
        with patch.object(storage, "get_conn") as get_conn:
            get_conn.return_value.execute.return_value.fetchall.return_value = []
            with self.assertRaises(HTTPException) as raised:
                app.api_image_detail(SimpleNamespace(headers={}), 999)
        self.assertEqual(raised.exception.status_code, 404)

    def test_preview_geometry_and_box_share_the_upright_coordinate_space(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            schema, "DB_PATH", str(Path(temp_dir) / "images.sqlite")
        ):
            image_path = Path(temp_dir) / "mirror-portrait.jpg"
            exif = Image.Exif()
            exif[274] = 7
            Image.new("RGB", (100, 80)).save(image_path, exif=exif)
            schema.init_db()
            conn = schema.get_conn()
            try:
                conn.execute(
                    "INSERT INTO image(id,path,directory,filename) VALUES (85,?,?,?)",
                    (str(image_path), temp_dir, image_path.name),
                )
                conn.execute(
                    "INSERT INTO face(image_id,bbox_x,bbox_y,bbox_w,bbox_h) VALUES (85,10,20,15,30)"
                )
                conn.commit()
            finally:
                conn.close()

            result = app.api_image_detail(SimpleNamespace(headers={}), 85)
            self.assertEqual((result["image_width"], result["image_height"]), (80, 100))
            self.assertEqual(
                tuple(result["faces"][0][key] for key in ("bbox_x", "bbox_y", "bbox_w", "bbox_h")),
                (30, 75, 30, 15),
            )

    def test_face_crop_revalidates_when_the_source_cache_key_changes(self):
        row = {"image_id": 85, "bbox_x": 1, "bbox_y": 2, "bbox_w": 3, "bbox_h": 4}
        with patch.object(app, "get_conn") as get_conn, patch.object(
            app, "get_available_image_path", return_value="/photos/current.jpg"
        ), patch.object(app, "ensure_face_thumbnail", return_value="/cache/42-source-a.jpg") as crop:
            get_conn.return_value.execute.return_value.fetchone.return_value = row
            first = app.api_face_crop(42, SimpleNamespace(headers={}))
            etag = first.headers["etag"]
            self.assertEqual(first.headers["cache-control"], "private, no-cache")
            self.assertEqual(
                app.api_face_crop(42, SimpleNamespace(headers={"if-none-match": etag})).status_code,
                304,
            )
            crop.return_value = "/cache/42-source-b.jpg"
            changed = app.api_face_crop(42, SimpleNamespace(headers={"if-none-match": etag}))
            self.assertEqual(changed.status_code, 200)
            self.assertNotEqual(changed.headers["etag"], etag)


if __name__ == "__main__":
    unittest.main()
