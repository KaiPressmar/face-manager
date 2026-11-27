import os
import tempfile
import threading
import unittest
from contextlib import ExitStack, closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import HTTPException
from PIL import Image

from backend import app
from backend.db import schema
from backend.services import storage
from backend.services.pipeline import ImportFailed, ImportProcessor


class PhotoIdentityTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        resources = Mock()
        self.model = resources.get_model.return_value
        self.model.detect_and_embed.return_value = []
        self.processor = ImportProcessor(resources)
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.pictures = self.root / "photos"
        self.pictures.mkdir()
        stack.enter_context(patch.object(schema, "DB_PATH", str(self.root / "library.sqlite")))
        stack.enter_context(patch.dict(os.environ, {"FACE_MANAGER_DATA_DIR": str(self.root / "data")}))
        schema.init_db(scan_sources=False)

    def import_pictures(self):
        self.processor.process(str(self.pictures), lambda _: None, threading.Event())

    def create_photo(self):
        path = self.pictures / "a.jpg"
        Image.new("RGB", (64, 48), "red").save(path)
        self.import_pictures()
        with closing(schema.get_conn()) as conn:
            photo = conn.execute("SELECT id, content_hash FROM image").fetchone()
        face_id, _ = storage.create_manual_face(
            photo["id"], photo["content_hash"], (1, 64, 48), (10, 12, 15, 16), "Anna",
        )
        with closing(schema.get_conn()) as conn, conn:
            conn.execute("INSERT INTO album(id, name, created_at, updated_at) VALUES ('a', 'Familie', 'now', 'now')")
            conn.execute("INSERT INTO album_photo(album_id, image_id, position) VALUES ('a', ?, 0)", (photo["id"],))
        return path, photo, face_id

    def assert_links_preserved(self, photo, face_id):
        with closing(schema.get_conn()) as conn:
            self.assertEqual(conn.execute("SELECT image_id FROM face WHERE id = ?", (face_id,)).fetchone()[0], photo["id"])
            self.assertEqual(conn.execute("SELECT image_id FROM album_photo").fetchone()[0], photo["id"])

    @staticmethod
    def add_jpeg_comment(path):
        original = path.read_bytes()
        comment = b"updated metadata"
        path.write_bytes(original[:2] + b"\xff\xfe" + (len(comment) + 2).to_bytes(2, "big") + comment + original[2:])

    @staticmethod
    async def response_body(response):
        chunks = []

        async def send(message):
            if message["type"] == "http.response.body":
                chunks.append(message["body"])

        await response({"type": "http", "asgi": {"spec_version": "2.4"}}, None, send)
        return b"".join(chunks)

    async def original_body(self, image_id):
        return await self.response_body(await app.get_image(image_id))

    async def test_metadata_only_edit_keeps_manual_face_album_and_verified_original(self):
        path, photo, face_id = self.create_photo()
        with Image.open(path) as encoded:
            pixels = encoded.tobytes()
        self.add_jpeg_comment(path)
        with Image.open(path) as encoded:
            self.assertEqual(encoded.tobytes(), pixels)
        with self.assertRaises(HTTPException) as changed:
            await self.original_body(photo["id"])
        self.assertEqual(changed.exception.status_code, 409)

        self.import_pictures()
        self.assert_links_preserved(photo, face_id)
        self.assertEqual(await self.original_body(photo["id"]), path.read_bytes())
        self.assertEqual((await app.api_manual_face_preview(photo["id"])).headers[app.PREVIEW_ORIGINAL_HEADER], "yes")

        renamed_copy = path.with_name("b.jpg")
        renamed_copy.write_bytes(path.read_bytes())
        self.import_pictures()
        with closing(schema.get_conn()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM image").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM image_location").fetchone()[0], 2)
        self.model.detect_and_embed.assert_called_once()

    async def test_true_replacement_leaves_singleton_and_uses_verified_duplicate(self):
        path, photo, face_id = self.create_photo()
        original = path.read_bytes()
        Image.new("RGB", (64, 48), "blue").save(path)
        with self.assertRaisesRegex(ImportFailed, "alte Bibliothekseintrag bleibt erhalten"):
            self.import_pictures()
        self.assert_links_preserved(photo, face_id)

        copy = path.with_name("b.jpg")
        copy.write_bytes(original)
        # Import the recovered copy by its new path, leaving the previous
        # manual and album references on the original library photo.
        self.import_pictures()
        self.assert_links_preserved(photo, face_id)
        self.assertEqual(await self.original_body(photo["id"]), original)
        with patch.object(app, "open_file_location") as reveal:
            with self.assertRaises(HTTPException) as missing_or_changed:
                app.open_image_location(photo["id"], {"image_path": str(path)})
            self.assertIn(missing_or_changed.exception.status_code, (404, 409))
            reveal.assert_not_called()

    async def test_unchanged_legacy_reimport_can_backfill_visual_evidence(self):
        path, photo, face_id = self.create_photo()
        with closing(schema.get_conn()) as conn, conn:
            conn.execute("UPDATE image SET visual_hash = NULL")
        self.import_pictures()
        self.add_jpeg_comment(path)
        self.import_pictures()
        self.assert_links_preserved(photo, face_id)
        self.assertEqual(await self.original_body(photo["id"]), path.read_bytes())
        self.model.detect_and_embed.assert_called_once()

    async def test_legacy_metadata_edit_can_be_proven_with_an_unchanged_copy(self):
        path, photo, face_id = self.create_photo()
        alternate_root = self.root / "alternate"
        alternate_root.mkdir()
        alternate = alternate_root / path.name
        alternate.write_bytes(path.read_bytes())
        self.processor.process(str(alternate_root), lambda _: None, threading.Event())
        with closing(schema.get_conn()) as conn, conn:
            conn.execute("UPDATE image SET visual_hash = NULL")
        self.add_jpeg_comment(path)
        self.import_pictures()
        self.assert_links_preserved(photo, face_id)
        self.assertEqual(storage.get_available_image_path(photo["id"], str(path), require_preferred=True), str(path))
        self.assertEqual(storage.get_available_image_path(photo["id"], str(alternate), require_preferred=True),
                         str(alternate))
        self.model.detect_and_embed.assert_called_once()

    async def test_verified_original_supports_byte_ranges(self):
        path, photo, _ = self.create_photo()
        response = await app.get_image(photo["id"], SimpleNamespace(headers={"range": "bytes=10-20"}))
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.headers["content-range"], f"bytes 10-20/{path.stat().st_size}")
        self.assertEqual(await self.response_body(response), path.read_bytes()[10:21])

    async def test_orientation_only_change_requires_explicit_resolution(self):
        path, photo, face_id = self.create_photo()
        exif = Image.Exif()
        exif[274] = 6
        Image.new("RGB", (64, 48), "red").save(path, exif=exif)
        with self.assertRaisesRegex(ImportFailed, "alte Bibliothekseintrag bleibt erhalten"):
            self.import_pictures()
        self.assert_links_preserved(photo, face_id)
        self.model.detect_and_embed.assert_called_once()
