import asyncio
import hashlib
import os
import tempfile
import unittest
from contextlib import closing
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from PIL import Image

from backend import app
from backend.db import schema
from backend.services import storage


BOX = {'x': 0.15, 'y': 0.25, 'width': 0.20, 'height': 0.25, 'person_name': '  Alex  '}


class ManualFaceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        database = patch.object(schema, 'DB_PATH', str(self.root / 'test.sqlite'))
        database.start()
        self.addCleanup(database.stop)
        storage.app_cache.clear()
        self.addCleanup(storage.app_cache.clear)
        schema.init_db(scan_sources=False)
        queue = patch.object(app.auto_cluster_queue, 'request_cancel')
        queue.start()
        self.addCleanup(queue.stop)
        events = patch.object(app, 'notify_clusters_changed')
        self.changed = events.start()
        self.addCleanup(events.stop)
        thumbnail_root = patch('backend.services.image_thumbnails.get_data_root', return_value=self.root)
        thumbnail_root.start()
        self.addCleanup(thumbnail_root.stop)

    async def from_preview(self):
        response = await app.api_manual_face_preview(1)
        return {**BOX, 'preview_token': response.headers['X-Face-Preview']}

    def cache_preview(self, content_hash, orientation):
        with Image.new('RGB', (120, 80), 'teal') as image:
            app.write_image_thumbnail(1, content_hash, image, orientation)

    def register(self, *, geometry=None, content_hash=None, make_file=False):
        picture = self.root / 'photo.jpg'
        if make_file:
            exif = Image.Exif()
            exif[274] = 7
            Image.new('RGB', (120, 80), 'teal').save(picture, exif=exif)
            if content_hash == 'verified':
                content_hash = hashlib.sha256(picture.read_bytes()).hexdigest()
        with closing(schema.get_conn()) as conn, conn:
            conn.execute(
                '''INSERT INTO image(id, path, directory, filename, content_hash, exif_orientation, raw_width, raw_height)
                   VALUES (1, ?, ?, ?, ?, ?, ?, ?)''',
                (str(picture), str(self.root), picture.name, content_hash, *(geometry or (None, None, None))),
            )
            conn.execute(
                'INSERT INTO image_location(image_id, path, directory, filename) VALUES (1, ?, ?, ?)',
                (str(picture), str(self.root), picture.name),
            )
        return picture, content_hash

    def test_raw_mapping_roundtrips_all_orientations(self):
        for orientation in range(1, 9):
            with self.subTest(orientation=orientation):
                geometry = (orientation, 120, 80)
                xywh = app._raw_manual_face_box(geometry, (0.15, 0.25, 0.20, 0.25))
                expected = (12, 30, 16, 30) if orientation >= 5 else (18, 20, 24, 20)
                self.assertEqual(app.correct_bbox_for_orientation(geometry, *xywh), expected)

    async def test_cached_preview_uses_registered_geometry_and_named_person(self):
        self.register(geometry=(6, 120, 80), content_hash='registered')
        self.cache_preview('registered', 6)
        preview = await app.api_manual_face_preview(1)
        self.assertEqual(preview.headers['X-Face-Preview-Original'], 'no')
        with closing(schema.get_conn()) as conn, conn:
            conn.execute("INSERT INTO person(id, name) VALUES (70, 'Alex')")
        with patch.object(app, '_run_source_work', side_effect=AssertionError('original accessed')):
            created = await app.api_create_manual_face(1, {
                **BOX, 'expected_content_hash': 'registered', 'preview_token': preview.headers['X-Face-Preview'],
            })
        photo = app.api_image_detail(SimpleNamespace(headers={}), 1)
        self.assertEqual((photo['image_width'], photo['image_height']), (80, 120))
        self.assertEqual(photo['faces'], [{
            'id': created['face_id'], 'bbox_x': 12.0, 'bbox_y': 30.0, 'bbox_w': 16.0, 'bbox_h': 30.0,
            'cluster_id': created['cluster_id'], 'person_name': 'Alex', 'review_status': 'active', 'is_manual': True,
        }])
        with closing(schema.get_conn()) as conn:
            row = conn.execute('SELECT f.embedding, f.is_manual, c.person_id FROM face f JOIN cluster c ON c.id = f.cluster_id').fetchone()
        self.assertEqual(tuple(row), (None, 1, 70))
        self.changed.assert_called_once_with('manual_face_created')

    async def test_legacy_photo_without_faces_resolves_and_persists_geometry(self):
        self.register(make_file=True)
        preview = await app.api_manual_face_preview(1)
        self.assertEqual(preview.headers['X-Face-Preview-Original'], 'yes')
        with Image.open(BytesIO(preview.body)) as upright:
            self.assertEqual(upright.size, (80, 120))
            self.assertIsNone(upright.getexif().get(274))
        await app.api_create_manual_face(1, {
            **BOX, 'expected_content_hash': None, 'preview_token': preview.headers['X-Face-Preview'],
        })
        row = storage.get_image_preview_source(1)
        self.assertEqual(app._stored_image_geometry(row), (7, 120, 80))
        self.assertIsNone(row['content_hash'])

    async def test_verified_pending_geometry_works_without_original_or_detected_faces(self):
        self.register(content_hash='registered')
        geometry = (8, 120, 80)
        self.cache_preview('registered', 8)
        with patch.object(app, '_pending_image_geometry', {(1, 'registered'): geometry}):
            detail = await app.get_image_detail(SimpleNamespace(headers={}), 1, resolve_geometry=True)
            self.assertEqual((detail['image_width'], detail['image_height']), (80, 120))
            await app.api_create_manual_face(1, await self.from_preview())
        self.assertEqual(app._stored_image_geometry(storage.get_image_preview_source(1)), geometry)

    async def test_invalid_or_stale_coordinates_do_not_create_faces(self):
        self.register(geometry=(1, 120, 80), content_hash='registered')
        self.cache_preview('registered', 1)
        mark = await self.from_preview()
        cases = [({'x': True}, 422), ({'width': 0}, 422), ({'x': float('nan')}, 422),
                 ({'y': 0.95}, 422), ({'width': 0.00001}, 422), ({'person_name': '  '}, 422),
                 ({'person_name': '\ud800'}, 422),
                 ({'preview_token': ''}, 409), ({'preview_token': mark['preview_token'] + 'a'}, 409),
                 ({'expected_content_hash': None}, 409), ({'expected_content_hash': 'stale'}, 409)]
        for data, status in cases:
            with self.subTest(data=data), self.assertRaises(HTTPException) as error:
                await app.api_create_manual_face(1, {**mark, **data})
            self.assertEqual(error.exception.status_code, status)
        self.assertEqual(storage.get_recorded_face_boxes(1), [])
        self.changed.assert_not_called()

    async def test_duplicate_concurrent_writes_are_atomic_and_group_faces_can_overlap(self):
        self.register(geometry=(1, 120, 80), content_hash='registered')
        self.cache_preview('registered', 1)
        mark = await self.from_preview()
        outcomes = await asyncio.gather(
            app.api_create_manual_face(1, mark),
            app.api_create_manual_face(1, {**mark, 'person_name': 'Taylor'}),
            return_exceptions=True,
        )
        successful = [outcome for outcome in outcomes if isinstance(outcome, dict)]
        conflicts = [outcome for outcome in outcomes if isinstance(outcome, HTTPException)]
        self.assertEqual(len(successful), 1)
        self.assertEqual([conflict.status_code for conflict in conflicts], [409])
        with closing(schema.get_conn()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM person').fetchone()[0], 1)
        await app.api_create_manual_face(1, {**mark, 'x': 0.27, 'person_name': 'Riley'})
        self.assertEqual(len(storage.get_recorded_face_boxes(1)), 2)
        self.assertEqual(self.changed.call_count, 2)

    async def test_registered_same_size_replacement_never_reaches_marking_editor(self):
        picture, content_hash = self.register(make_file=True, content_hash='verified', geometry=(7, 120, 80))
        original_stat = picture.stat()
        original_bytes = picture.read_bytes()
        picture.write_bytes(original_bytes[:-1] + b'\x00')
        os.utime(picture, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        self.assertEqual(len(picture.read_bytes()), len(original_bytes))
        self.assertNotEqual(picture.read_bytes(), original_bytes)
        with self.assertRaises(HTTPException) as error:
            await app.api_manual_face_preview(1)
        self.assertEqual(error.exception.status_code, 422)

        self.cache_preview(content_hash, 7)
        preview = await app.api_manual_face_preview(1)
        self.assertEqual(preview.headers['X-Face-Preview-Original'], 'no')
        await app.api_create_manual_face(1, {**BOX, 'preview_token': preview.headers['X-Face-Preview']})

    async def test_legacy_replacement_invalidates_previously_rendered_preview(self):
        picture, _ = self.register(make_file=True)
        mark = await self.from_preview()
        original = picture.read_bytes()
        replacement = picture.with_suffix('.temporary')
        replacement.write_bytes(original[:-1] + b'\x00')
        replacement.replace(picture)
        with self.assertRaises(HTTPException) as error:
            await app.api_create_manual_face(1, mark)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(storage.get_recorded_face_boxes(1), [])

    async def test_cached_preview_with_wrong_aspect_cannot_authorize_marking(self):
        self.register(geometry=(7, 120, 80), content_hash='registered')
        self.cache_preview('registered', 1)
        with self.assertRaises(HTTPException) as error:
            await app.api_manual_face_preview(1)
        self.assertEqual(error.exception.status_code, 422)

    async def test_suggestion_passes_archived_boxes_and_ignores_geometry_backfill(self):
        picture, content_hash = self.register(make_file=True, content_hash='verified')
        with closing(schema.get_conn()) as conn, conn:
            conn.execute("INSERT INTO face(image_id, bbox_x, bbox_y, bbox_w, bbox_h, review_status) VALUES (1, 10, 20, 30, 40, 'not_face')")
        suggestions = [{'x': 0.5, 'y': 0.5, 'width': 0.20, 'height': 0.25}]

        def suggest(paths, registered_hash, boxes, _get_model):
            self.assertEqual(paths, [str(picture)])
            self.assertEqual(registered_hash, content_hash)
            self.assertEqual(boxes, [(10.0, 20.0, 30.0, 40.0)])
            storage.save_missing_image_geometry(1, content_hash, (7, 120, 80))
            return suggestions

        with patch.object(app, 'suggest_face_boxes', side_effect=suggest):
            self.assertEqual(await app.api_face_box_suggestions(1), {'items': suggestions})
        self.changed.assert_not_called()
