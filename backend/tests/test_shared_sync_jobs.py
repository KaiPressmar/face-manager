from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
from threading import Barrier
import unittest
from unittest.mock import patch
from uuid import uuid4

from backend.services import shared_sync_jobs as jobs
from backend.services import shared_sync_transport as sync


class SharedSyncJobsTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.transport = sync.ShareTransport(self.root, allow_writes=True)
        if not self.transport.probe_writes():
            self.skipTest("Local filesystem has no supported immutable publication")
        self.manifest = self.transport.open(create=True)
        self.job = jobs.make_job_descriptor(self.manifest, "1" * 64, "2" * 64)
        self.owner = jobs.SharedJobScheduler(self.transport, uuid4().hex, uuid4().hex)

    def another_session(self, *, same_device=False):
        return jobs.SharedJobScheduler(
            sync.ShareTransport(self.root, allow_writes=True),
            self.owner.device_id if same_device else uuid4().hex,
            uuid4().hex,
        )

    def test_job_identity_is_deterministic_and_validated(self):
        self.assertEqual(jobs.make_job_descriptor(self.manifest, "1" * 64, "2" * 64), self.job)
        for photo, pipeline in (("z" * 64, "2" * 64), ("1" * 64, "F" * 64), ("1" * 64, "v1")):
            with self.assertRaises(sync.InvalidTransportData):
                jobs.make_job_descriptor(self.manifest, photo, pipeline)
        revision = jobs.make_job_descriptor(self.manifest, "1" * 64, "3" * 64)
        self.assertNotEqual(revision["job_id"], self.job["job_id"])
        with self.assertRaises(sync.InvalidTransportData):
            self.owner.inspect({**self.job, "scope": uuid4().hex})
        with self.assertRaises(sync.InvalidTransportData):
            self.owner.inspect({**self.job, "version": True})

    def test_published_jobs_can_be_discovered_alongside_unrelated_events(self):
        producer = uuid4().hex
        other = jobs.make_job_descriptor(self.manifest, "3" * 64, "2" * 64)
        emitted = jobs.make_job_event(self.manifest, other)
        jobs.append_job_batch(self.transport, producer, 1, 1, [self.job])
        self.transport.append_events(producer, 2, 2, [{"type": "other"}, emitted, emitted])
        self.transport.append_events(producer, 3, 5, [{"type": "other"}])
        reader = sync.ShareTransport(self.root)
        self.assertEqual(jobs.read_job_batch(reader, producer, 1), [self.job])
        self.assertEqual(jobs.read_job_batch(reader, producer, 2), [other])
        self.assertEqual(jobs.read_job_batch(reader, producer, 3), [])
        self.assertIsNone(jobs.read_job_batch(reader, producer, 4))
        self.transport.append_events(producer, 4, 6, [{"type": "analysis-job", "job": {}}])
        with self.assertRaises(sync.InvalidTransportData):
            jobs.read_job_batch(reader, producer, 4)

    def test_two_sessions_compete_for_one_claim(self):
        other = self.another_session()
        ready = Barrier(2)
        rename = sync._rename_no_replace

        def synchronized(directory, source, destination):
            if destination == "claim.json":
                ready.wait(5)
            return rename(directory, source, destination)

        with patch.object(sync, "_rename_no_replace", side_effect=synchronized):
            with ThreadPoolExecutor(2) as pool:
                first, second = pool.map(lambda scheduler: scheduler.claim(self.job), (self.owner, other))
        self.assertEqual({first["state"], second["state"]}, {"owned", "reserved"})
        self.assertEqual(first["claim"], second["claim"])

    def test_reissued_attempt_rejects_old_result_and_requires_fresh_proof(self):
        self.assertEqual(self.owner.claim(self.job)["attempt"], 1)
        result = {"faces": []}
        old_digest = self.owner.stage_result(self.job, 1, result)
        self.assertIsNone(self.owner.get_result(self.job))
        other = self.another_session(same_device=True)
        self.assertEqual(other.claim(self.job)["state"], "reserved")
        with self.assertRaises(sync.ReadOnlyTransportError):
            other.observe(self.job, 1)
        with patch.object(sync.time, "monotonic", return_value=100):
            observed = other.observe(self.job, 1, server_fresh=True)
        with patch.object(sync.time, "monotonic", return_value=170):
            with self.assertRaises(sync.ReadOnlyTransportError):
                other.expire(self.job, observed)
            self.assertEqual(other.expire(self.job, observed, server_fresh=True)["state"], "expired")
        self.assertEqual(other.claim(self.job)["attempt"], 2)
        with self.assertRaises(sync.PublishConflictError):
            self.owner.finish_result(self.job, 1, old_digest)
        with self.assertRaises(sync.InvalidTransportData):
            other.finish_result(self.job, 2, old_digest)
        other.publish_result(self.job, 2, {"faces": ["winner"]})
        self.assertEqual(self.owner.get_result(self.job), {"faces": ["winner"]})
        self.assertEqual(self.owner.claim(self.job)["state"], "completed")

    def test_lost_claim_result_and_terminal_receipts_leave_retryable_work(self):
        claim = self.transport.claim_attempt

        def claim_then_disconnect(*args):
            claim(*args)
            raise sync.UnknownPublishError("lost claim acknowledgement")

        with patch.object(self.transport, "claim_attempt", side_effect=claim_then_disconnect):
            with self.assertRaises(sync.UnknownPublishError):
                self.owner.claim(self.job)
        self.assertEqual(self.owner.claim(self.job)["state"], "owned")
        store = self.transport.put_object

        def stage_then_disconnect(*args):
            store(*args)
            raise sync.UnknownPublishError("lost object acknowledgement")

        result = {"faces": [{"approved": True}]}
        with patch.object(self.transport, "put_object", side_effect=stage_then_disconnect):
            with self.assertRaises(sync.UnknownPublishError):
                self.owner.stage_result(self.job, 1, result)
        self.assertIsNone(self.owner.get_result(self.job))
        digest = self.owner.stage_result(self.job, 1, result)
        finish = self.transport.finish_attempt

        def finish_then_disconnect(*args):
            finish(*args)
            raise sync.UnknownPublishError("lost decision acknowledgement")

        with patch.object(self.transport, "finish_attempt", side_effect=finish_then_disconnect):
            with self.assertRaises(sync.UnknownPublishError):
                self.owner.finish_result(self.job, 1, digest)
        self.assertEqual(self.owner.publish_result(self.job, 1, result)["result_digest"], digest)
        with self.assertRaises(sync.PublishConflictError):
            self.owner.publish_result(self.job, 1, {"faces": [{"approved": 1}]})
        self.assertEqual(self.another_session().get_result(self.job), result)

    def test_job_stops_after_bounded_attempts(self):
        for attempt in range(1, sync.MAX_ATTEMPTS + 1):
            self.assertEqual(self.owner.claim(self.job)["attempt"], attempt)
            with patch.object(sync.time, "monotonic", return_value=attempt * 100):
                observation = self.owner.observe(self.job, attempt, server_fresh=True)
            with patch.object(sync.time, "monotonic", return_value=attempt * 100 + 61):
                self.owner.expire(self.job, observation, server_fresh=True)
        self.assertEqual(self.owner.claim(self.job), {"state": "exhausted", "attempt": sync.MAX_ATTEMPTS})


if __name__ == "__main__":
    unittest.main()
