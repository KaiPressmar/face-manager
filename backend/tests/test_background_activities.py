import asyncio
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from backend.db import schema
from backend.services import geo
from backend.services.background_activities import BackgroundActivities
from backend.services.events import EventHub


class PhotoMetadataActivityTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        database = patch.object(schema, "DB_PATH", str(self.folder / "photos.sqlite"))
        database.start()
        self.addCleanup(database.stop)
        schema.init_db(scan_sources=False)
        self.hub = EventHub()
        self.worker = geo.GeoBackfill(BackgroundActivities(self.hub, progress_interval=0))

    def _seed(self, *available):
        with closing(schema.get_conn()) as conn:
            for index, exists in enumerate(available, 1):
                path = self.folder / f"photo-{index}.jpg"
                if exists:
                    Image.new("RGB", (8, 8)).save(path)
                conn.execute("INSERT INTO image (path, directory, filename) VALUES (?, ?, ?)",
                             (str(path), str(self.folder), path.name))
            conn.commit()

    def test_empty_scan_does_not_publish_an_activity(self):
        with patch.object(self.hub, "publish", wraps=self.hub.publish) as publish:
            self.worker._run()
        publish.assert_not_called()

    def test_progress_counts_commits_and_replays_recent_completion(self):
        self._seed(True, False)
        persisted_counts = []
        wire_events = []
        real_save = geo._save_capture_metadata
        real_publish = self.hub.publish
        failed_once = False

        def save(conn, image_id, metadata):
            nonlocal failed_once
            if image_id == 2 and not failed_once:
                failed_once = True
                raise sqlite3.OperationalError("database is locked")
            return real_save(conn, image_id, metadata)

        def observe(topic, payload):
            self.assertEqual(topic, "background-activities")
            wire_events.append(payload)
            with closing(schema.get_conn()) as conn:
                persisted_counts.append(conn.execute(
                    "SELECT COUNT(*) FROM image WHERE metadata_scanned_at IS NOT NULL"
                ).fetchone()[0])
            real_publish(topic, payload)

        with patch.object(geo, "_save_capture_metadata", side_effect=save), patch.object(
            self.hub, "publish", side_effect=observe,
        ):
            self.worker._run()
        states = [event["activities"][0] for event in wire_events]
        self.assertTrue(failed_once)
        self.assertEqual(persisted_counts, [0, 1, 2, 2])
        self.assertEqual([state["processed"] for state in states], persisted_counts)
        self.assertEqual([state["status"] for state in states], ["running", "running", "running", "completed"])
        self.assertEqual({state["id"] for state in states}, {states[0]["id"]})
        self.assertEqual(states[-1], {
            "id": states[0]["id"], "kind": "photo-metadata", "status": "completed", "processed": 2,
            "total": 2, "unavailable": 1, "finished_at": states[-1]["finished_at"],
        })
        self.assertIsNone(states[0]["finished_at"])
        self.assertEqual(datetime.fromisoformat(states[-1]["finished_at"]).tzinfo, timezone.utc)

        async def reconnect():
            stream = self.hub.stream()
            try:
                frame = await anext(stream)
                self.assertTrue(frame.startswith("event: background-activities\n"))
                self.assertEqual(json.loads(frame.splitlines()[1][6:]), wire_events[-1])
            finally:
                await stream.aclose()

        asyncio.run(reconnect())

    def test_an_empty_followup_keeps_real_completion(self):
        self._seed(True)
        with patch.object(self.hub, "publish", wraps=self.hub.publish) as publish:
            self.worker._run()
            calls = publish.call_count
            self.assertGreater(calls, 0)
            self.worker._run()
            self.assertEqual(publish.call_count, calls)

    def test_unrecoverable_scan_error_publishes_failure(self):
        self._seed(True)
        with patch.object(self.hub, "publish", wraps=self.hub.publish) as publish, patch.object(
            self.worker, "_scan_batch", side_effect=sqlite3.OperationalError("disk I/O error"),
        ), self.assertLogs("face_manager.geo", level="ERROR"):
            self.worker._run()
        failure = publish.call_args.args[1]["activities"][0]
        self.assertEqual((failure["status"], failure["processed"], failure["total"]), ("failed", 0, 1))

    def test_interrupted_scan_reports_committed_progress(self):
        self._seed(True, True)
        statuses = []

        def observe(topic, payload):
            status = payload["activities"][0]
            statuses.append(status)
            if status["status"] == "running" and status["processed"] == 1:
                self.worker._stop.set()

        with patch.object(self.hub, "publish", side_effect=observe):
            self.worker._run()
        cancelled = statuses[-1]
        self.assertEqual((cancelled["status"], cancelled["processed"], cancelled["total"]), ("cancelled", 1, 2))


if __name__ == "__main__":
    unittest.main()
