from contextlib import closing
from pathlib import Path
import tempfile
from threading import Event, Thread
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

import numpy as np
from PIL import Image

from backend.db import schema
from backend.services import shared_sync_job_announcements as queue
from backend.services import shared_sync_transport as sync
from backend.services.pipeline import ImportProcessor
from backend.services.shared_sync_import import ImportShare, SharedImportAnalysis
from backend.services.shared_sync_jobs import make_job_descriptor
from backend.services.shared_sync_worker import run_request


class _InlineWorker:
    def request(self, payload, *, staging_root=None):
        return run_request(payload, staging_root=staging_root)


class JobAnnouncementsTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.share = self.root / "share"
        self.share.mkdir()
        self.photos = self.root / "photos"
        self.photos.mkdir()
        self.transport = sync.ShareTransport(self.share, allow_writes=True)
        if not self.transport.probe_writes():
            self.skipTest("Local filesystem lacks immutable slot publication")
        self.manifest = self.transport.open(create=True)

    def test_announcements_are_idempotent_paged_and_keep_distinct_aliases(self):
        job = make_job_descriptor(self.manifest, "a" * 64, "b" * 64)
        one = queue.publish_job_announcement(self.transport, job, "album/Übung.jpg")
        self.assertEqual(queue.publish_job_announcement(self.transport, job, "album/Übung.jpg"), one)
        two = queue.publish_job_announcement(self.transport, job, "other/photo.jpg")
        self.assertNotEqual(one["announcement_id"], two["announcement_id"])
        self.assertEqual(self.transport.list_producers(), [])
        page = queue.list_job_announcements(self.transport, limit=1)
        self.assertFalse(page["at_end"])
        second = queue.list_job_announcements(self.transport, after=page["after"], limit=1)
        self.assertTrue(second["at_end"])
        self.assertEqual(
            [entry["announcement_id"] for entry in page["announcements"] + second["announcements"]],
            sorted([one["announcement_id"], two["announcement_id"]]),
        )
        for unsafe in ("../outside.jpg", "C:/outside.jpg", "child/.FM/file.jpg", "x/CON.jpg", "child//photo"):
            with self.subTest(unsafe=unsafe), self.assertRaises(sync.InvalidTransportData):
                queue.publish_job_announcement(self.transport, job, unsafe)

    def binding(self, **fields):
        return ImportShare(
            str(self.photos), str(self.share), self.manifest, uuid4().hex, uuid4().hex, **fields,
        )

    @staticmethod
    def coordinator(binding, *, on_reused=None):
        with patch("backend.services.shared_sync_import.SharedSyncLane", side_effect=_InlineWorker):
            return SharedImportAnalysis(
                lambda _path: binding, available_shares=lambda: (binding,), on_reused=on_reused,
            )

    def test_exclusions_are_enforced_before_loading_model_or_claiming(self):
        hidden = self.photos / "private"
        binding = self.binding(excluded_roots=(str(hidden),))
        scout = self.coordinator(binding)
        job = make_job_descriptor(self.manifest, "a" * 64, "b" * 64)
        queue.publish_job_announcement(self.transport, job, "private/portrait.jpg")
        load_model = Mock(side_effect=AssertionError("excluded work loaded the model"), spec=lambda: None)
        self.assertEqual(
            scout.assist(binding, load_model),
            {"inspected": 1, "completed": 0, "pending": 0, "more": False},
        )
        self.assertFalse(binding.contains_path(str(hidden / "portrait.jpg")))
        self.assertFalse(scout.available_for(str(hidden / "portrait.jpg")))
        self.assertIsNone(self.transport.get_claim(job["job_id"], 1))

    def test_dispatch_receives_staging_root_only_for_a_current_binding(self):
        binding = self.binding()
        current = [binding]
        dispatch = Mock(return_value={"status": "verified"})
        helper = SharedImportAnalysis(
            lambda _path: current[0], available_shares=lambda: tuple(filter(None, current)), dispatch=dispatch,
        )
        lane = Mock()
        payload = {"action": "stage_source"}
        request = helper._request_for(binding, lane)
        self.assertEqual(request(payload, staging_root=self.root), {"status": "verified"})
        dispatch.assert_called_once_with(binding, lane, payload, staging_root=self.root)
        lane.request.assert_not_called()
        current[0] = None
        self.assertEqual(request(payload, staging_root=self.root), {"status": "unavailable"})
        dispatch.assert_called_once()

    def test_one_import_exposes_lookahead_and_reuses_assistant_results(self):
        for index, color in enumerate(("red", "green", "blue")):
            Image.new("RGB", (48, 32), color).save(self.photos / f"photo-{index}.jpg")
        producer, assistant = self.binding(device_name="Büro-PC"), self.binding(device_name="Wohnzimmer-PC")
        reported = Mock()
        announcing = self.coordinator(producer, on_reused=reported)
        assisting = self.coordinator(assistant)
        started, release = Event(), Event()
        failures = []
        model = Mock()
        peer_model = Mock()
        model.shared_pipeline_signature.return_value = "b" * 64
        peer_model.shared_pipeline_signature.return_value = "b" * 64
        vector = np.zeros(512, dtype=np.float32)
        vector[0] = 1
        face = {"bbox": (4, 4, 20, 20), "embedding": vector}

        def infer(_pixels):
            started.set()
            if not release.wait(5):
                raise TimeoutError("assistant did not complete")
            return [face]

        model.detect_and_embed.side_effect = infer
        peer_model.detect_and_embed.return_value = [face]
        resources = Mock()
        resources.get_model.return_value = model
        resources.get_clusterer.return_value.add_and_assign.return_value = (np.array([1]), None)
        db = self.root / "library.sqlite"

        def import_folder():
            try:
                ImportProcessor(resources, shared_analysis=announcing).process(
                    str(self.photos), lambda _changes: None, Event(),
                )
            except Exception as exc:
                failures.append(exc)

        with patch.object(schema, "DB_PATH", str(db)):
            schema.init_db(scan_sources=False)
            thread = Thread(target=import_folder)
            thread.start()
            try:
                self.assertTrue(started.wait(4))
                self.assertEqual(len(queue.list_job_announcements(self.transport)["announcements"]), 3)
                with patch.object(ImportProcessor, "process", side_effect=AssertionError("assistant invoked an import")):
                    progress = assisting.assist(assistant, peer_model)
                self.assertEqual(progress, {"inspected": 3, "completed": 2, "pending": 1, "more": False})
                self.assertEqual(peer_model.detect_and_embed.call_count, 2)
            finally:
                release.set()
                thread.join(6)
            self.assertFalse(thread.is_alive())
            self.assertEqual(failures, [])
            model.detect_and_embed.assert_called_once()
            self.assertEqual(reported.call_count, 2)
            reported.assert_called_with(producer, "Wohnzimmer-PC")
            with closing(schema.get_conn()) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM image").fetchone()[0], 3)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM face").fetchone()[0], 3)


if __name__ == "__main__":
    unittest.main()
