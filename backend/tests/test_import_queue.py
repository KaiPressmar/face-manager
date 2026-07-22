import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.services.import_queue import (
    ImportJob,
    ImportJobRepository,
    ImportQueue,
    LiveStageTiming,
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
        progress_callback(
            {
                "stage": "processing",
                "stage_current": 1,
                "stage_total": 2,
                "current_file": f"{folder_path}/photo.jpg",
                "total_images": 2,
                "processed_images": 1,
            }
        )
        try:
            while not self.release.wait(0.01):
                wait_if_paused = getattr(cancel_event, "wait_if_paused", None)
                if callable(wait_if_paused) and wait_if_paused():
                    raise ImportCancelled()
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
        running = next(job for job in snapshot["jobs"] if job["id"] == first["id"])
        self.assertEqual(running["stage"], "processing")
        self.assertEqual(running["stage_current"], 1)
        self.assertEqual(running["current_file"], "/photos/first/photo.jpg")
        self.assertIsNotNone(running["elapsed_seconds"])
        self.assertTrue(
            all(station["job_id"] == first["id"] for station in running["stations"])
        )

        self.processor.release.set()
        self.wait_for(lambda: len(self.processor.started) == 2)
        self.assertEqual(first["status"], "queued")

    def test_jobs_can_run_in_parallel_when_configured(self):
        self.processor.release.clear()
        self.queue.stop()
        self.queue = ImportQueue(
            self.processor,
            repository=self.repository,
            max_concurrent_jobs=2,
        )

        self.queue.enqueue("/photos/first")
        self.queue.enqueue("/photos/second")
        self.wait_for(lambda: len(self.processor.started) == 2)

        snapshot = self.queue.snapshot()
        self.assertEqual(snapshot["running_count"], 2)
        self.assertEqual(snapshot["max_concurrent_jobs"], 2)
        self.assertEqual(self.processor.max_active_count, 2)

    def test_overall_eta_uses_parallel_critical_path_instead_of_sum(self):
        self.queue.stop()
        self.queue = ImportQueue(
            self.processor,
            repository=self.repository,
            auto_start=False,
            max_concurrent_jobs=2,
        )
        first = self.queue.enqueue("/photos/first")
        second = self.queue.enqueue("/photos/second")
        third = self.queue.enqueue("/photos/third")
        durations = {
            "/photos/first": 120.0,
            "/photos/second": 90.0,
            "/photos/third": 60.0,
        }

        with patch.object(
            self.queue,
            "_estimate_job_total_seconds",
            side_effect=lambda job, _average: durations[job.folder_path],
        ):
            snapshot = self.queue.snapshot()

        etas = {job["id"]: job["eta_seconds"] for job in snapshot["jobs"]}
        self.assertEqual(etas[first["id"]], 120)
        self.assertEqual(etas[second["id"]], 90)
        self.assertEqual(etas[third["id"]], 150)
        self.assertEqual(snapshot["overall_eta_seconds"], 150)

    def test_large_import_eta_uses_recent_throughput_and_ignores_outlier(self):
        job = ImportJob(
            id="large-import",
            folder_path="/photos/large",
            status="running",
            created_at="2026-01-01T00:00:00+00:00",
            stage="processing",
            total_images=1000,
            processed_images=100,
            stage_current=100,
            stage_total=1000,
        )
        tracker = LiveStageTiming(
            stage="processing",
            last_progress=100,
            last_sample_at=100.0,
            samples=[1.0, 1.0, 1.0, 1.0, 1.0],
        )
        self.queue._live_stage_timing[job.id] = tracker

        first_eta = self.queue._estimate_stage_remaining_seconds(job, 1000)
        tracker.observe(110, 110.0)
        job.processed_images = 110
        job.stage_current = 110
        steady_eta = self.queue._estimate_stage_remaining_seconds(job, 1000)

        tracker.observe(111, 210.0)
        job.processed_images = 111
        job.stage_current = 111
        outlier_eta = self.queue._estimate_stage_remaining_seconds(job, 1000)

        self.assertEqual(first_eta, 900.0)
        self.assertLess(steady_eta, first_eta)
        self.assertLess(outlier_eta, steady_eta)

    def test_live_eta_calibrates_from_first_completed_interval(self):
        tracker = LiveStageTiming(
            stage="processing",
            last_progress=0,
            last_sample_at=10.0,
        )

        tracker.observe(2, 14.0)

        self.assertEqual(tracker.seconds_per_unit, 2.0)

    def test_resume_rebases_live_eta_sample_after_pause(self):
        job = self.queue.enqueue("/photos/paused-eta")
        self.wait_for(lambda: self.processor.started == ["/photos/paused-eta"])
        stored_job = self.queue._jobs[job["id"]]
        tracker = LiveStageTiming(
            stage="processing",
            last_progress=stored_job.processed_images,
            last_sample_at=10.0,
            samples=[1.0, 1.0, 1.0],
        )
        self.queue._live_stage_timing[job["id"]] = tracker

        self.queue.pause(job["id"])
        with patch("backend.services.import_queue.time.monotonic", return_value=1000.0):
            self.queue.resume(job["id"])

        self.assertEqual(tracker.last_sample_at, 1000.0)
        self.assertEqual(tracker.seconds_per_unit, 1.0)

    def test_on_change_fires_on_enqueue_and_progress(self):
        self.processor.release.clear()
        self.queue.stop()
        changes = threading.Event()
        change_count = {"n": 0}

        def on_change():
            change_count["n"] += 1
            changes.set()

        self.queue = ImportQueue(
            self.processor,
            repository=self.repository,
            on_change=on_change,
        )

        changes.clear()
        self.queue.enqueue("/photos/notify")
        self.assertTrue(changes.wait(1.0), "enqueue did not notify on_change")
        # Progress updates from the running job must also notify subscribers.
        self.wait_for(lambda: self.processor.started == ["/photos/notify"])
        self.assertGreaterEqual(change_count["n"], 2)

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
        self.wait_for(lambda: self.queue.snapshot()["jobs"][0]["status"] == "cancelled")

    def test_running_job_can_be_paused_resumed_and_cancelled(self):
        job = self.queue.enqueue("/photos/running")
        self.wait_for(lambda: self.processor.started == ["/photos/running"])

        paused = self.queue.pause(job["id"])
        self.assertEqual(paused["status"], "paused")
        self.assertEqual(self.queue.snapshot()["jobs"][0]["status"], "paused")

        resumed = self.queue.resume(job["id"])
        self.assertEqual(resumed["status"], "running")
        self.assertEqual(self.queue.snapshot()["jobs"][0]["status"], "running")

        self.queue.pause(job["id"])
        cancelling = self.queue.cancel(job["id"])
        self.assertEqual(cancelling["status"], "cancelling")
        self.wait_for(lambda: self.queue.snapshot()["jobs"][0]["status"] == "cancelled")

    def test_paused_queued_job_keeps_its_place_until_resumed(self):
        self.queue.enqueue("/photos/running")
        paused_job = self.queue.enqueue("/photos/paused")
        later_job = self.queue.enqueue("/photos/later")
        self.wait_for(lambda: self.processor.started == ["/photos/running"])

        self.queue.pause(paused_job["id"])
        self.processor.release.set()
        self.wait_for(lambda: "/photos/later" in self.processor.started)
        self.assertNotIn("/photos/paused", self.processor.started)

        self.queue.resume(paused_job["id"])
        self.wait_for(lambda: "/photos/paused" in self.processor.started)
        jobs = {job["id"]: job for job in self.queue.snapshot()["jobs"]}
        self.assertIn(jobs[later_job["id"]]["status"], {"running", "completed"})

    def test_terminal_history_can_be_deleted_individually_or_together(self):
        self.processor.release.set()
        first = self.queue.enqueue("/photos/first")
        second = self.queue.enqueue("/photos/second")
        self.wait_for(
            lambda: all(
                job["status"] == "completed" for job in self.queue.snapshot()["jobs"]
            )
        )

        self.assertEqual(self.queue.delete_terminal(first["id"])["status"], "removed")
        self.assertEqual(self.queue.clear_history(), 1)
        self.assertEqual(self.queue.snapshot()["jobs"], [])

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
            lambda: (
                len(self.processor.started) >= 4
                and self.queue.snapshot()["queued_count"] == 0
            )
        )
        self.wait_for(
            lambda: all(
                job["status"] == "completed" for job in self.queue.snapshot()["jobs"]
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

    def test_repository_migrates_existing_import_job_table(self):
        self.queue.stop()
        legacy_path = Path(self.temp_dir.name) / "legacy.sqlite"
        connection = sqlite3.connect(legacy_path)
        connection.executescript(
            """
            CREATE TABLE import_job (
                id TEXT PRIMARY KEY,
                folder_path TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                total_images INTEGER NOT NULL DEFAULT 0,
                processed_images INTEGER NOT NULL DEFAULT 0,
                total_faces INTEGER NOT NULL DEFAULT 0,
                processed_faces INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                queue_order INTEGER NOT NULL
            );
            """
        )
        connection.close()

        def legacy_connection_factory():
            legacy_connection = sqlite3.connect(legacy_path)
            legacy_connection.row_factory = sqlite3.Row
            return legacy_connection

        ImportJobRepository(legacy_connection_factory)
        connection = legacy_connection_factory()
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(import_job)").fetchall()
        }
        connection.close()

        self.assertTrue(
            {
                "stage",
                "stage_started_at",
                "stage_current",
                "stage_total",
                "current_file",
            }.issubset(columns)
        )
