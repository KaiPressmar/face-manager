import threading
import unittest
from unittest.mock import Mock

from backend.services.person_suggestion_worker import PersonSuggestionWorker


class PersonSuggestionWorkerTest(unittest.TestCase):
    def test_skips_refresh_when_busy_and_wakes_when_ready(self):
        busy_checked = threading.Event()
        ready = threading.Event()
        published = threading.Event()

        def is_ready():
            if ready.is_set():
                return True
            busy_checked.set()
            return False

        refresh = Mock(return_value=True)
        worker = PersonSuggestionWorker(is_ready, refresh, published.set, interval_seconds=60)
        self.addCleanup(worker.stop)
        worker.start()
        self.assertTrue(busy_checked.wait(2))
        refresh.assert_not_called()

        ready.set()
        worker.wake()
        self.assertTrue(published.wait(2))
        worker.stop()
        refresh.assert_called_once_with()

    def test_wakes_during_refresh_coalesce_into_one_check(self):
        refreshing = threading.Event()
        release = threading.Event()
        published = threading.Event()

        def run_refresh():
            if refreshing.is_set():
                return True
            refreshing.set()
            release.wait(2)
            return False

        refresh = Mock(side_effect=run_refresh)
        worker = PersonSuggestionWorker(lambda: True, refresh, published.set, interval_seconds=60)
        self.addCleanup(worker.stop)
        self.addCleanup(release.set)
        worker.start()
        self.assertTrue(refreshing.wait(2))
        worker.start()
        for _ in range(5):
            worker.wake()
        release.set()

        self.assertTrue(published.wait(2))
        worker.stop()
        self.assertEqual(refresh.call_count, 2)

    def test_poll_retries_failed_refresh_and_notification(self):
        published = threading.Event()
        attempts = 0

        def publish():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("temporary notification failure")
            published.set()

        refresh_results = iter([RuntimeError("temporary refresh failure"), True])

        def refresh():
            result = next(refresh_results, False)
            if isinstance(result, Exception):
                raise result
            return result

        worker = PersonSuggestionWorker(lambda: True, refresh, publish, interval_seconds=0.01)
        self.addCleanup(worker.stop)
        with self.assertLogs("face_manager.person_suggestion_worker", level="ERROR") as logs:
            worker.start()
            self.assertTrue(published.wait(2))
            worker.stop()
        self.assertEqual(len(logs.records), 2)
        self.assertEqual(attempts, 2)

    def test_stop_and_restart_check_for_persisted_work(self):
        checked = threading.Event()

        def refresh():
            checked.set()
            return False

        publish = Mock()
        worker = PersonSuggestionWorker(lambda: True, refresh, publish, interval_seconds=60)
        self.addCleanup(worker.stop)
        self.assertFalse(worker.stop())
        worker.start()
        self.assertTrue(checked.wait(2))
        self.assertTrue(worker.stop())
        self.assertFalse(worker.stop())
        checked.clear()

        worker.wake()
        self.assertFalse(checked.is_set())
        worker.start()
        self.assertTrue(checked.wait(2))
        worker.stop()
        publish.assert_not_called()
