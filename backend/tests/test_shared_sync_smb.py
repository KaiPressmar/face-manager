"""Transport qualification using two independently mounted CIFS clients."""

import os
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from backend.services import shared_sync_transport
from backend.services.shared_sync_transport import PublishConflictError, ShareTransport


class SharedSyncSmbTest(unittest.TestCase):
    def _race(self, slot, operations):
        barrier = threading.Barrier(2)
        rename = shared_sync_transport._rename_no_replace

        def synchronized_publication(directory, source, destination):
            if destination == slot:
                barrier.wait(timeout=10)
            return rename(directory, source, destination)

        with patch.object(shared_sync_transport, "_rename_no_replace", synchronized_publication):
            with ThreadPoolExecutor(max_workers=2) as workers:
                futures = [workers.submit(operation) for operation in operations]
                return [future.result(timeout=30) for future in futures]

    @unittest.skipUnless(
        os.environ.get("FACE_MANAGER_TEST_SMB_FIRST") and os.environ.get("FACE_MANAGER_TEST_SMB_SECOND"),
        "Two independently mounted CIFS clients are required",
    )
    def test_clients_share_one_manifest_and_only_one_attempt_wins(self):
        first_path = Path(os.environ["FACE_MANAGER_TEST_SMB_FIRST"])
        second_path = Path(os.environ["FACE_MANAGER_TEST_SMB_SECOND"])
        self.assertNotEqual(first_path, second_path)
        first, second = (ShareTransport(path, allow_writes=True) for path in (first_path, second_path))
        self.assertTrue(first.probe_writes(), "First CIFS mount lacks atomic no-replace publication")
        self.assertTrue(second.probe_writes(), "Second CIFS mount lacks atomic no-replace publication")
        left, right = self._race("manifest.json", [lambda: first.open(True), lambda: second.open(True)])
        self.assertEqual(left, right)
        self.assertEqual(left, ShareTransport(first_path).open())
        self.assertEqual(left, ShareTransport(second_path).open())

        job_id = uuid.uuid4().hex

        def claim(client):
            device, session = uuid.uuid4().hex, uuid.uuid4().hex
            try:
                return client.claim_attempt(job_id, 1, device, session)
            except PublishConflictError:
                return None

        claims = self._race("claim.json", [lambda: claim(first), lambda: claim(second)])
        accepted, = [record for record in claims if record is not None]
        self.assertEqual(first.get_claim(job_id, 1), accepted)
        self.assertEqual(second.get_claim(job_id, 1), accepted)

        producer = uuid.uuid4().hex
        events = [{"operation": "identical retry"}]
        alpha, beta = self._race("00000000000000000001.json", [
            lambda: first.append_events(producer, 1, 1, events),
            lambda: second.append_events(producer, 1, 1, events),
        ])
        self.assertEqual(alpha, beta)
        self.assertEqual(second.get_event_batch(producer, 1), alpha)
