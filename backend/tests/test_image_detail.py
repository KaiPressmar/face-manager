import asyncio
import hashlib
from io import BytesIO
import tempfile
import threading
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


class ImageDetailWithoutFacesTest(unittest.IsolatedAsyncioTestCase):
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

    async def test_preview_geometry_and_box_share_the_upright_coordinate_space(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            schema, "DB_PATH", str(Path(temp_dir) / "images.sqlite")
        ):
            image_path = Path(temp_dir) / "mirror-portrait.jpg"
            exif = Image.Exif()
            exif[274] = 7
            Image.new("RGB", (100, 80)).save(image_path, exif=exif)
            content_hash = hashlib.sha256(image_path.read_bytes()).hexdigest()
            schema.init_db()
            conn = schema.get_conn()
            try:
                conn.execute(
                    "INSERT INTO image(id,path,directory,filename,content_hash) VALUES (85,?,?,?,?)",
                    (str(image_path), temp_dir, image_path.name, content_hash),
                )
                conn.executemany(
                    "INSERT INTO image_location(image_id,path,directory,filename) VALUES (85,?,?,?)",
                    [
                        (str(Path(temp_dir) / "a-corrupt-first.jpg"), temp_dir, "a-corrupt-first.jpg"),
                        (str(image_path), temp_dir, image_path.name),
                    ],
                )
                conn.execute(
                    "INSERT INTO face(image_id,bbox_x,bbox_y,bbox_w,bbox_h) VALUES (85,10,20,15,30)"
                )
                conn.commit()
            finally:
                conn.close()

            corrupt = Path(temp_dir) / "a-corrupt-first.jpg"
            corrupt.write_bytes(b"not an image")
            request = SimpleNamespace(headers={})
            with (
                patch("backend.services.image_thumbnails.get_data_root", return_value=Path(temp_dir)),
                patch("backend.services.face_thumbnails.get_data_root", return_value=Path(temp_dir)),
            ):
                before = app.api_image_detail(request, 85)
                self.assertIsNone(before["image_width"])
                self.assertEqual(len(before["faces"]), 1)
                photo = await app.get_image_thumbnail(85, request)
                face = await app.api_face_crop(1, request)
                with Image.open(BytesIO(photo.body)) as thumbnail:
                    self.assertEqual(thumbnail.size, (80, 100))
                image_path.unlink()
                corrupt.unlink()
                with patch.object(app, "_run_source_work", side_effect=AssertionError("original touched")):
                    result = app.api_image_detail(request, 85)
                    gallery = app.get_images(
                        request, folders=[], persons=[], face_statuses=[], limit=40, offset=0, all_photos=True,
                    )
                    self.assertEqual(gallery["items"][0]["image_width"], 80)
                    self.assertEqual((await app.get_image_thumbnail(85, request)).body, photo.body)
                    face_etag = SimpleNamespace(headers={"if-none-match": face.headers["etag"]})
                    self.assertEqual((await app.api_face_crop(1, face_etag)).status_code, 304)
            self.assertEqual((result["image_width"], result["image_height"]), (80, 100))
            self.assertEqual(
                tuple(result["faces"][0][key] for key in ("bbox_x", "bbox_y", "bbox_w", "bbox_h")),
                (30, 75, 30, 15),
            )

    async def test_legacy_face_crop_revalidates_when_the_source_cache_key_changes(self):
        row = {"image_id": 85, "content_hash": None, "bbox_x": 1, "bbox_y": 2, "bbox_w": 3, "bbox_h": 4}
        with tempfile.TemporaryDirectory() as folder, patch.object(app, "get_face_preview_source", return_value=row), patch.object(
            app, "list_image_locations", return_value={85: [{"path": "/photos/current.jpg"}]}
        ), patch.object(app, "ensure_face_thumbnail") as crop:
            root = Path(folder) / "thumbnails/faces/0000"
            root.mkdir(parents=True)
            source_a = root / ("42-" + "a" * 24 + ".jpg")
            source_b = root / ("42-" + "b" * 24 + ".jpg")
            source_a.write_bytes(b"preview a")
            source_b.write_bytes(b"preview b")
            crop.return_value = source_a
            first = await app.api_face_crop(42, SimpleNamespace(headers={}))
            etag = first.headers["etag"]
            self.assertEqual(first.headers["cache-control"], "private, no-cache")
            self.assertEqual(
                (await app.api_face_crop(42, SimpleNamespace(headers={"if-none-match": etag}))).status_code,
                304,
            )
            crop.return_value = source_b
            changed = await app.api_face_crop(42, SimpleNamespace(headers={"if-none-match": etag}))
            self.assertEqual(changed.status_code, 200)
            self.assertNotEqual(changed.headers["etag"], etag)

    async def test_late_preview_from_a_replaced_library_does_not_publish(self):
        generation = app._get_media_library_generation()
        reading = threading.Event()
        release = threading.Event()

        def blocked_decode(*_):
            reading.set()
            if not release.wait(3):
                raise RuntimeError("test did not release source read")
            return Image.new("RGB", (16, 8)), (1, 16, 8)

        with (
            patch.object(app, "get_image_preview_source", return_value={"image_id": 5, "content_hash": "old"}),
            patch.object(app, "get_cached_image_thumbnail", return_value=None),
            patch.object(app, "list_image_locations", return_value={5: [{"path": "/older/book.jpg"}]}),
            patch.object(app, "decode_verified_preview", side_effect=blocked_decode),
            patch.object(app, "write_image_thumbnail") as write,
            patch.object(app, "save_missing_image_geometry") as save,
        ):
            preview = asyncio.create_task(app.get_image_thumbnail(5, SimpleNamespace(headers={})))
            try:
                self.assertTrue(await asyncio.to_thread(reading.wait, 2))
                with app._media_library_lock:
                    app._media_library_generation = generation + 1
            finally:
                release.set()
            with self.assertRaises(HTTPException) as raised:
                await preview
            self.assertEqual(raised.exception.status_code, 409)
            write.assert_not_called()
            save.assert_not_called()

    async def test_timed_out_source_workers_do_not_block_warm_thumbnail(self):
        release = threading.Event()
        workers = [threading.Event() for _ in range(app._SOURCE_WORKER_COUNT)]
        finished = [threading.Event() for _ in workers]

        def blocked_source(index):
            workers[index].set()
            release.wait(3)
            finished[index].set()

        with tempfile.TemporaryDirectory() as folder:
            cached = Path(folder) / ("thumbnails/images/0000/5-" + "a" * 20 + ".jpg")
            cached.parent.mkdir(parents=True)
            cached.write_bytes(b"warm preview")
            with (
                patch.object(app, "_SOURCE_TIMEOUT_SECONDS", 0.2),
                patch.object(app, "get_image_preview_source", return_value={"image_id": 5, "content_hash": "hash"}),
                patch.object(app, "get_cached_image_thumbnail", return_value=cached),
            ):
                pending = [
                    asyncio.create_task(app._run_source_work(lambda i=index: blocked_source(i)))
                    for index in range(len(workers))
                ]
                try:
                    self.assertTrue(all(await asyncio.gather(*[asyncio.to_thread(event.wait, 2) for event in workers])))
                    timeouts = await asyncio.gather(*pending, return_exceptions=True)
                    self.assertEqual([error.status_code for error in timeouts], [503] * len(workers))
                    self.assertFalse(any(event.is_set() for event in finished))
                    response = await app.get_image_thumbnail(5, SimpleNamespace(headers={}))
                    self.assertEqual(response.body, b"warm preview")
                finally:
                    release.set()
                    await asyncio.gather(*[asyncio.to_thread(event.wait, 2) for event in finished])

    def test_path_cleanup_defers_while_warmup_has_an_active_task(self):
        with (
            patch.object(app, "is_backend_idle_for_thumbnail_warmup", return_value=True),
            patch.object(app.face_thumbnail_warmup_queue, "snapshot", return_value={"task": {"status": "running"}}),
            patch.object(app.face_thumbnail_warmup_queue, "has_active_batch", return_value=False),
        ):
            self.assertFalse(app.is_backend_idle_for_path_cleanup())


if __name__ == "__main__":
    unittest.main()
