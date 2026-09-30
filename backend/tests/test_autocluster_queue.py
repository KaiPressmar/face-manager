"""Behaviour of the clustering queue's never-drop scheduling.

The queue must accept every reclustering request. While the SQLite writer is
busy (an import is running) a request stays visibly ``queued`` and starts on its
own once the readiness gate opens. Requests that arrive while another pass runs
are coalesced into a single pending slot, with higher priority winning.
"""

import threading
import time
import unittest

from backend.services.autocluster_queue import (
    AutoClusterQueue,
    PRIORITY_IDLE_RECLUSTER,
    PRIORITY_MANUAL_RECLUSTER,
)


def _wait_for(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class AutoClusterQueueTest(unittest.TestCase):
    def _new_queue(self, **kwargs) -> AutoClusterQueue:
        queue = AutoClusterQueue(**kwargs)
        self.addCleanup(self._join_worker, queue)
        return queue

    @staticmethod
    def _join_worker(queue: AutoClusterQueue) -> None:
        queue.request_cancel()
        for _ in range(3):
            with queue._lock:
                thread = queue._thread
            if thread is None:
                return
            thread.join(2.0)
            if not thread.is_alive():
                return

    def _status(self, queue: AutoClusterQueue):
        task = queue.snapshot()["task"]
        return task["status"] if task else None

    def test_request_runs_immediately_when_gate_open(self):
        ran = threading.Event()
        queue = self._new_queue(
            count_callable=lambda: 5,
            repair_callable=lambda progress_callback=None: ran.set() or 5,
            ready_gate=lambda: True,
        )

        task = queue.start("manual", kind="full_recluster")

        self.assertIsNotNone(task)
        self.assertTrue(ran.wait(2.0))
        self.assertTrue(_wait_for(lambda: self._status(queue) == "completed"))

    def test_request_stays_queued_until_gate_opens(self):
        gate_open = threading.Event()
        ran = threading.Event()
        queue = self._new_queue(
            count_callable=lambda: 5,
            repair_callable=lambda progress_callback=None: ran.set() or 5,
            ready_gate=gate_open.is_set,
        )

        task = queue.start("manual", kind="full_recluster")

        # Accepted and visible as queued, but not started while the gate is shut.
        self.assertEqual(task["status"], "queued")
        self.assertFalse(ran.wait(0.2))
        self.assertEqual(self._status(queue), "queued")

        # Opening the gate and nudging the queue starts the deferred task.
        gate_open.set()
        queue.notify_ready()
        self.assertTrue(ran.wait(2.0))
        self.assertTrue(_wait_for(lambda: self._status(queue) == "completed"))

    def test_request_never_returns_none_while_work_exists(self):
        queue = self._new_queue(
            count_callable=lambda: 0,
            repair_callable=lambda progress_callback=None: 0,
            ready_gate=lambda: True,
        )

        self.assertIsNone(queue.start("manual", kind="full_recluster"))

    def test_higher_priority_request_supersedes_deferred_one(self):
        gate_open = threading.Event()
        seen_kinds: list[str] = []

        def repair(progress_callback=None):
            seen_kinds.append("ran")
            return 3

        queue = self._new_queue(
            count_callable=lambda: 3,
            repair_callable=repair,
            ready_gate=gate_open.is_set,
        )

        queue.start("idle", kind="unassigned_recluster", priority=PRIORITY_IDLE_RECLUSTER)
        superseded = queue.start(
            "manual", kind="full_recluster", priority=PRIORITY_MANUAL_RECLUSTER
        )

        self.assertEqual(superseded["kind"], "full_recluster")
        self.assertEqual(self._status(queue), "queued")

    def test_pending_request_runs_after_active_task_finishes(self):
        release_first = threading.Event()
        first_started = threading.Event()
        second_ran = threading.Event()

        def first(progress_callback=None):
            first_started.set()
            release_first.wait(2.0)
            return 4

        def second(progress_callback=None):
            second_ran.set()
            return 2

        queue = self._new_queue(
            count_callable=lambda: 4,
            repair_callable=first,
            ready_gate=lambda: True,
        )

        queue.start("first", kind="full_recluster", priority=PRIORITY_IDLE_RECLUSTER)
        self.assertTrue(first_started.wait(2.0))

        # Arrives while the first pass runs: it must be coalesced, not dropped.
        queue.start(
            "second",
            kind="full_recluster",
            count_callable=lambda: 2,
            repair_callable=second,
            priority=PRIORITY_MANUAL_RECLUSTER,
        )
        self.assertFalse(second_ran.is_set())

        release_first.set()
        self.assertTrue(second_ran.wait(2.0))

    def test_running_task_can_pause_resume_cancel_and_be_dismissed(self):
        started = threading.Event()

        def repair(progress_callback=None, cancel_token=None):
            started.set()
            processed = 0
            while processed < 20:
                if cancel_token.wait_if_paused() or cancel_token.is_set():
                    return processed
                processed += 1
                progress_callback(processed, 20)
                time.sleep(0.01)
            return processed

        queue = self._new_queue(
            count_callable=lambda: 20,
            repair_callable=repair,
            ready_gate=lambda: True,
        )
        task = queue.start("manual", kind="full_recluster")
        self.assertTrue(started.wait(1))

        self.assertEqual(queue.pause(task["id"])["status"], "paused")
        paused_progress = queue.snapshot()["task"]["processed_faces"]
        time.sleep(0.05)
        self.assertEqual(queue.snapshot()["task"]["processed_faces"], paused_progress)

        self.assertEqual(queue.resume(task["id"])["status"], "running")
        self.assertTrue(_wait_for(lambda: queue.snapshot()["task"]["processed_faces"] > paused_progress))
        queue.pause(task["id"])
        queue.cancel(task["id"])
        self.assertTrue(_wait_for(lambda: self._status(queue) == "cancelled"))
        self.assertTrue(queue.dismiss(task["id"]))
        self.assertIsNone(queue.snapshot()["task"])


if __name__ == "__main__":
    unittest.main()
