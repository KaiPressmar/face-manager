import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path

from backend.services.import_queue import (
    ImportJob,
    ImportJobRepository,
    ImportQueue,
)
from backend.services.pipeline import ImportCancelled


class RecordingProcessor:
    def __init__(self):
        self.started = []
        self.release = threading.Event()
        self.active_count = 0
        self.max_active_count = 0

    def process(self, folder_path, progress_callback, cancel_event):
        self.started.append(folder_path)
        self.active_count += 1
        self.max_active_count = max(self.max_active_count, self.active_count)
        progress_callback({"total_images": 2, "processed_images": 1})
        try:
            while not self.release.wait(0.01):
                if cancel_event.is_set():
                    raise ImportCancelled()
        finally:
            self.active_count -= 1


class ImportQueueTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "queue.sqlite"

        def connection_factory():
            connection = sqlite3.connect(self.db_path, timeout=30)
            connection.row_factory = sqlite3.Row
            return connection

        self.repository = ImportJobRepository(connection_factory)
        self.processor = RecordingProcessor()
        self.queue = ImportQueue(
            self.processor,
            repository=self.repository,
        )

    def tearDown(self):
        self.processor.release.set()
        self.queue.stop()
        self.temp_dir.cleanup()

    def wait_for(self, predicate, timeout=1.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        self.fail("Timed out waiting for queue state")

    def test_jobs_run_serially_in_queue_order(self):
        first = self.queue.enqueue("/photos/first")
        second = self.queue.enqueue("/photos/second")
        self.wait_for(lambda: self.processor.started == ["/photos/first"])

        snapshot = self.queue.snapshot()
        queued = next(job for job in snapshot["jobs"] if job["id"] == second["id"])
        self.assertEqual(queued["queue_position"], 1)
        self.assertEqual(self.processor.max_active_count, 1)

        self.processor.release.set()
        self.wait_for(lambda: len(self.processor.started) == 2)
        self.assertEqual(first["status"], "queued")

    def test_queued_job_can_be_removed(self):
        self.queue.enqueue("/photos/running")
        queued = self.queue.enqueue("/photos/remove")
        self.wait_for(lambda: self.processor.started == ["/photos/running"])

        result = self.queue.cancel_or_remove(queued["id"])

        self.assertEqual(result["status"], "removed")
        self.assertNotIn(
            queued["id"],
            [job["id"] for job in self.queue.snapshot()["jobs"]],
        )

    def test_running_job_can_be_cancelled(self):
        job = self.queue.enqueue("/photos/running")
        self.wait_for(lambda: self.processor.started == ["/photos/running"])

        result = self.queue.cancel_or_remove(job["id"])
        self.assertEqual(result["status"], "cancelling")
        self.wait_for(
            lambda: self.queue.snapshot()["jobs"][0]["status"] == "cancelled"
        )

    def test_terminal_history_is_bounded(self):
        self.processor.release.set()
        self.queue.stop()
        self.queue = ImportQueue(
            self.processor,
            repository=self.repository,
            history_limit=2,
        )

        for index in range(4):
            self.queue.enqueue(f"/photos/{index}")

        self.wait_for(
            lambda: len(self.processor.started) >= 4
            and self.queue.snapshot()["queued_count"] == 0
        )
        self.wait_for(
            lambda: all(
                job["status"] == "completed"
                for job in self.queue.snapshot()["jobs"]
            )
        )

        jobs = self.queue.snapshot()["jobs"]
        self.assertEqual(len(jobs), 2)
        self.assertEqual(
            [job["folder_path"] for job in jobs],
            ["/photos/2", "/photos/3"],
        )

    def test_interrupted_job_is_requeued_after_restart(self):
        self.processor.release.set()
        self.queue.stop()
        interrupted = ImportJob(
            id="interrupted",
            folder_path="/photos/interrupted",
            status="running",
            created_at="2026-01-01T00:00:00+00:00",
            started_at="2026-01-01T00:01:00+00:00",
            total_images=100,
            processed_images=40,
            total_faces=20,
            processed_faces=20,
        )
        queued = ImportJob(
            id="queued",
            folder_path="/photos/queued",
            status="queued",
            created_at="2026-01-01T00:02:00+00:00",
        )
        self.repository.insert(interrupted)
        self.repository.insert(queued)

        restarted_processor = RecordingProcessor()
        restarted_processor.release.set()
        self.processor = restarted_processor
        self.queue = ImportQueue(
            restarted_processor,
            repository=self.repository,
            auto_start=False,
        )

        snapshot = self.queue.snapshot()
        recovered = snapshot["jobs"][0]
        self.assertEqual(recovered["status"], "queued")
        self.assertIsNone(recovered["started_at"])
        self.assertEqual(recovered["processed_images"], 0)
        self.assertEqual(snapshot["queued_count"], 2)

        self.queue.start()
        self.wait_for(lambda: len(restarted_processor.started) == 2)
        self.assertEqual(
            restarted_processor.started,
            ["/photos/interrupted", "/photos/queued"],
        )
