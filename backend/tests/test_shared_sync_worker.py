import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from backend.services import shared_sync_transport as transport
from backend.services import shared_sync_worker as worker
from backend.services.shared_sync_jobs import make_job_descriptor


class SourceStageTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.photo_root = self.root / "originals"
        self.photo_root.mkdir()
        self.ipc = self.root / "ipc"
        self.ipc.mkdir(mode=0o700)
        self.source = self.photo_root / "foto.jpg"
        self.source.write_bytes(b"registered photo contents")
        self.output = self.ipc / "staged.jpg"
        self.request = {
            "action": "stage_source", "source_root": str(self.photo_root),
            "relative_path": self.source.name, "output_path": str(self.output),
            "expected_hash": hashlib.sha256(self.source.read_bytes()).hexdigest(),
        }

    def test_cli_stages_the_verified_bytes_in_the_ipc_input_directory(self):
        request_path = self.ipc / "request.json"
        request_path.write_text(json.dumps(self.request), encoding="utf-8")
        result_path = self.root / "result.json"
        self.assertEqual(worker.main([str(request_path), str(result_path)]), 0)
        result = json.loads(result_path.read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["photo_sha256"], self.request["expected_hash"])
        self.assertEqual(result["identity"][2], self.source.stat().st_size)
        self.assertEqual(result["output_path"], str(self.output))
        self.assertEqual(self.output.read_bytes(), self.source.read_bytes())

    def test_mismatch_or_failed_copy_leaves_no_partial_output(self):
        mismatch = {**self.request, "expected_hash": "f" * 64}
        self.assertEqual(worker.run_request(mismatch, staging_root=self.ipc), {"status": "source_changed"})
        self.assertFalse(self.output.exists())

        def fail_after_bytes(_request, output):
            output.write(b"partial")
            raise OSError("staging interrupted")

        with patch.object(worker, "_verify_source", side_effect=fail_after_bytes):
            with self.assertRaises(OSError):
                worker.run_request(self.request, staging_root=self.ipc)
        self.assertFalse(self.output.exists())

    def test_conflict_previews_bind_photo_content_and_raw_geometry(self):
        from PIL import Image

        Image.new("RGB", (80, 60), "#42798b").save(self.source)
        request = {
            "action": "conflict_preview", "source_root": str(self.photo_root), "relative_path": self.source.name,
            "expected_hash": hashlib.sha256(self.source.read_bytes()).hexdigest(), "geometry": [1, 80, 60],
            "boxes": {"local": [0.1, 0.1, 0.5, 0.7], "shared": [0.12, 0.1, 0.52, 0.7]},
        }
        result = worker.run_request(request)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(set(result["images"]), {"photo", "local", "shared"})
        self.assertTrue(all(value.startswith("data:image/jpeg;base64,") for value in result["images"].values()))
        self.assertEqual(worker.run_request({**request, "geometry": [6, 80, 60]}), {"status": "source_changed"})
        Image.new("RGB", (80, 60), "#ffa915").save(self.source)
        self.assertEqual(worker.run_request(request), {"status": "source_changed"})

    def test_cannot_write_outside_private_directory_or_overwrite_existing_files(self):
        with self.assertRaises(ValueError):
            worker.run_request({**self.request, "output_path": str(self.source)}, staging_root=self.ipc)
        with self.assertRaises(ValueError):
            worker.run_request(self.request)
        self.output.write_bytes(b"previous contents")
        with self.assertRaises(FileExistsError):
            worker.run_request(self.request, staging_root=self.ipc)
        self.assertEqual(self.output.read_bytes(), b"previous contents")

    def test_redirected_source_is_rejected_and_partial_target_removed(self):
        alias = self.photo_root / "alias.jpg"
        try:
            alias.symlink_to(self.source)
        except OSError:
            self.skipTest("Symbolic links are unavailable")
        with self.assertRaises((OSError, ValueError, transport.TransportError)):
            worker.run_request({**self.request, "relative_path": alias.name}, staging_root=self.ipc)
        self.assertFalse(self.output.exists())


class SharedJobWorkerTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        share = transport.ShareTransport(self.root, allow_writes=True)
        if not share.probe_writes():
            self.skipTest("Local filesystem has no supported immutable publication")
        self.manifest = share.open(create=True)
        self.request = {
            "action": "job", "sync_root": str(self.root), "known_manifest": self.manifest,
            "allow_writes": True, "device_id": uuid4().hex, "session_id": uuid4().hex,
            "job": make_job_descriptor(self.manifest, "1" * 64, "2" * 64),
        }

    def operation(self, name: str, **fields):
        response = worker.run_request({**self.request, "operation": name, **fields})
        self.assertEqual(response["status"], "job")
        self.assertEqual(response["operation"], name)
        return response["value"]

    def test_worker_fences_owner_and_commits_staged_result(self):
        self.assertEqual(self.operation("inspect", allow_writes=False)["state"], "pending")
        self.assertEqual(self.operation("claim")["state"], "owned")
        with self.assertRaises(transport.ReadOnlyTransportError):
            self.operation("claim", allow_writes=False)
        self.assertIsNone(self.operation("heartbeat", attempt=1, counter=1))
        self.assertIsNone(self.operation("get_result", allow_writes=False))
        with self.assertRaises(transport.PublishConflictError):
            self.operation("stage_result", attempt=1, session_id=uuid4().hex, result={})
        digest = self.operation("stage_result", attempt=1, result={"faces": []})
        self.assertIsNone(self.operation("get_result"))
        decision = self.operation("finish_result", attempt=1, result_digest=digest)
        self.assertEqual(decision["state"], "completed")
        self.assertEqual(self.operation("get_result", allow_writes=False), {"faces": []})
        self.assertEqual(self.operation("finish_result", attempt=1, result_digest=digest), decision)

    def test_job_requires_manifest_agreement_before_claim(self):
        with self.assertRaises(transport.InvalidTransportData):
            worker.run_request({**self.request, "operation": "claim", "known_manifest": None})
        self.assertEqual(
            worker.run_request({
                **self.request, "operation": "claim",
                "known_manifest": {**self.manifest, "generation": uuid4().hex},
            }),
            {"status": "share_changed"},
        )
        self.assertEqual(self.operation("inspect")["state"], "pending")

    def test_expiry_requires_valid_serialized_observation_and_fresh_proof(self):
        self.operation("claim")
        with self.assertRaises(transport.ReadOnlyTransportError):
            self.operation("observe", attempt=1)
        with patch.object(transport.time, "monotonic", return_value=100):
            observed = self.operation("observe", attempt=1, server_fresh=True)
        serialized = json.loads(json.dumps(observed))
        with patch.object(transport.time, "monotonic", return_value=170):
            with self.assertRaises(transport.ReadOnlyTransportError):
                self.operation("expire", observation=serialized)
            with self.assertRaises(transport.InvalidTransportData):
                self.operation("expire", observation={**serialized, "checked_at": float("inf")}, server_fresh=True)
            self.assertEqual(self.operation("expire", observation=serialized, server_fresh=True)["state"], "expired")
        self.assertEqual(self.operation("claim")["attempt"], 2)

    def test_reconcile_only_reads_fixed_slot_and_checks_exact_intent(self):
        producer = uuid4().hex
        request = {
            "action": "exchange", "sync_root": str(self.root), "known_manifest": self.manifest,
            "reconcile_only": True, "allow_writes": True, "producer_id": producer,
            "outgoing": {"batch": 1, "first_sequence": 1, "events": [{"ready": True}]},
        }
        with (
            patch.object(transport.ShareTransport, "append_events", side_effect=AssertionError("write")),
            patch.object(transport.ShareTransport, "probe_writes", side_effect=AssertionError("probe")),
            patch.object(transport.ShareTransport, "list_producers", side_effect=AssertionError("listing")),
        ):
            self.assertEqual(
                worker.run_request(request),
                {"status": "unpublished", "manifest": self.manifest},
            )
        self.assertFalse((self.root / ".fm" / "events").exists())
        share = transport.ShareTransport(self.root, allow_writes=True)
        share.open()
        published = share.append_events(producer, 1, 1, [{"ready": True}])
        answer = worker.run_request(request)
        self.assertEqual(answer["status"], "published")
        self.assertEqual(answer["batch"], published)
        self.assertEqual(answer["digest"], worker._json_digest(published))
        with self.assertRaises(transport.PublishConflictError):
            worker.run_request({
                **request, "outgoing": {"batch": 1, "first_sequence": 1, "events": [{"ready": 1}]},
            })


if __name__ == "__main__":
    unittest.main()
