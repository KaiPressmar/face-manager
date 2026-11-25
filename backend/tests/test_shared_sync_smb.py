"""Transport qualification using two independently mounted CIFS clients."""

import os
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from backend.services.shared_sync_transport import PublishConflictError, ShareTransport


class SharedSyncSmbTest(unittest.TestCase):
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
        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(client.open, True) for client in (first, second)]
            self.assertEqual(futures[0].result(), futures[1].result())
            barrier = threading.Barrier(2)
            job_id = uuid.uuid4().hex

            def claim(client):
                device, session = uuid.uuid4().hex, uuid.uuid4().hex
                barrier.wait(timeout=5)
                try:
                    return client.claim_attempt(job_id, 1, device, session)
                except PublishConflictError:
                    return None

            claims = [workers.submit(claim, client) for client in (first, second)]
            accepted, = [candidate for future in claims if (candidate := future.result()) is not None]
        self.assertEqual(first.get_claim(job_id, 1), accepted)
        self.assertEqual(second.get_claim(job_id, 1), accepted)
