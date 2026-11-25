import base64
import hashlib
from pathlib import Path
import tempfile
from threading import Event, Lock
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

import numpy as np
from PIL import Image

from backend.models import face_model
from backend.services import shared_sync_analysis as analysis
from backend.services import shared_sync_transport as transport
from backend.services import shared_sync_worker as worker


def face_sample():
    embedding = np.zeros(512, dtype=np.float32)
    embedding[0] = 1
    return {"bbox": (np.int32(5), 5, 10, 12), "embedding": embedding}


class SharedAnalysisTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name).resolve()
        self.source_root = root / "photos"
        self.source_root.mkdir()
        self.source = self.source_root / "example.jpg"
        exif = Image.Exif()
        exif[274] = 6
        Image.new("RGB", (64, 32), "teal").save(self.source, exif=exif)
        self.photo_sha256 = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.share = root / "share"
        self.share.mkdir()
        exchange = transport.ShareTransport(self.share, allow_writes=True)
        if not exchange.probe_writes():
            self.skipTest("Local filesystem has no supported immutable publication")
        self.manifest = exchange.open(create=True)
        self.model = Mock()
        self.model.shared_pipeline_signature.return_value = "1" * 64
        self.model.detect_and_embed.return_value = [face_sample()]
        self.requests = []

    def request(self, payload, *, staging_root=None):
        self.requests.append((payload["action"], payload.get("operation")))
        return worker.run_request(payload, staging_root=staging_root)

    def exchange(self, *, allow_writes=True, request=None, **options):
        return analysis.SharedAnalysisExchange(
            request=request or self.request, sync_root=self.share, source_root=self.source_root,
            manifest=self.manifest, device_id=uuid4().hex, session_id=uuid4().hex,
            allow_writes=allow_writes, **options,
        )

    def test_verified_local_inference_is_committed_then_reused_without_source(self):
        owner = self.exchange()
        outcome = owner.analyze_or_reuse(self.model, self.source.name, self.photo_sha256)
        self.assertEqual(outcome.state, "analyzed")
        self.assertEqual(outcome.geometry, (6, 64, 32))
        self.assertEqual(self.model.detect_and_embed.call_args.args[0].shape, (32, 64, 3))
        self.assertEqual(outcome.faces[0]["bbox"], (5, 5, 10, 12))
        self.assertEqual(outcome.faces[0]["embedding"].dtype, np.float32)
        self.assertEqual(self.requests.count(("stage_source", None)), 1)
        self.source.unlink()
        self.requests.clear()
        reused = self.exchange(allow_writes=False).analyze_or_reuse(
            self.model, self.source.name, self.photo_sha256
        )
        self.assertEqual(reused.state, "reused")
        self.assertEqual(self.requests, [("job", "get_result")])
        self.model.detect_and_embed.assert_called_once()
        np.testing.assert_array_equal(reused.faces[0]["embedding"], outcome.faces[0]["embedding"])

    def test_lost_stage_receipt_retries_serialized_result_without_new_inference(self):
        dropped = False

        def request(payload, *, staging_root=None):
            nonlocal dropped
            response = self.request(payload, staging_root=staging_root)
            if payload.get("operation") == "stage_result" and not dropped:
                dropped = True
                return {"status": "published_unknown"}
            return response

        owner = self.exchange(request=request)
        unknown = owner.analyze_or_reuse(self.model, self.source.name, self.photo_sha256)
        self.assertEqual(unknown.state, "published_unknown")
        self.assertIsNone(unknown.faces)
        self.source.unlink()
        accepted = owner.analyze_or_reuse(self.model, self.source.name, self.photo_sha256)
        self.assertEqual(accepted.state, "analyzed")
        self.assertEqual(self.requests.count(("stage_source", None)), 1)
        self.model.detect_and_embed.assert_called_once()

    def test_changed_source_and_pipeline_do_not_publish_an_analysis(self):
        photo_revision = self.source.read_bytes()
        self.source.write_bytes(b"disappeared photo revision")
        owner = self.exchange()
        self.assertEqual(
            owner.analyze_or_reuse(self.model, self.source.name, self.photo_sha256).state,
            "source_changed",
        )
        self.model.detect_and_embed.assert_not_called()
        self.source.write_bytes(photo_revision)
        self.model.shared_pipeline_signature.side_effect = ("1" * 64, "1" * 64, "2" * 64)
        with self.assertRaisesRegex(transport.InvalidTransportData, "pipeline changed during"):
            owner.analyze_or_reuse(self.model, self.source.name, self.photo_sha256)
        self.assertNotIn(("job", "stage_result"), self.requests)

    def test_separate_heartbeat_lane_renews_during_slow_inference(self):
        inference_started = Event()
        renewed = Event()

        def run_heartbeat(payload):
            response = self.request(payload)
            if inference_started.is_set():
                renewed.set()
            return response

        def slow_inference(_pixels):
            inference_started.set()
            self.assertTrue(renewed.wait(1))
            return [face_sample()]

        self.model.detect_and_embed.side_effect = slow_inference
        owner = self.exchange(heartbeat_request=run_heartbeat, heartbeat_seconds=0.01)
        outcome = owner.analyze_or_reuse(self.model, self.source.name, self.photo_sha256)
        self.assertEqual(outcome.state, "analyzed")

    def test_inference_after_hard_attempt_deadline_is_never_published(self):
        now = 100

        def late_inference(_pixels):
            nonlocal now
            now = 102
            return [face_sample()]

        self.model.detect_and_embed.side_effect = late_inference
        owner = self.exchange(max_attempt_seconds=1)
        with patch.object(analysis.time, "monotonic", side_effect=lambda: now):
            outcome = owner.analyze_or_reuse(self.model, self.source.name, self.photo_sha256)
        self.assertEqual(outcome.state, "pending")
        self.assertNotIn(("job", "stage_result"), self.requests)

    def test_reclaim_requires_repeated_fresh_observations_and_new_session(self):
        owner = self.exchange()
        job = owner.describe(self.model, self.photo_sha256)
        self.assertEqual(owner._job("claim", job)["attempt"], 1)
        successor = self.exchange()
        with patch.object(transport.time, "monotonic", return_value=100):
            self.assertEqual(successor.reclaim(job)["state"], "reserved")
        self.assertNotIn(("job", "observe"), self.requests)
        for now in (100, 120, 140):
            with patch.object(transport.time, "monotonic", return_value=now):
                self.assertEqual(successor.reclaim(job, server_fresh=True)["state"], "reserved")
        with patch.object(transport.time, "monotonic", return_value=161):
            reclaimed = successor.reclaim(job, server_fresh=True)
        self.assertEqual(reclaimed["state"], "owned")
        self.assertEqual(reclaimed["attempt"], 2)
        self.assertEqual(reclaimed["claim"]["session_id"], successor.session_id)

    def test_wire_rejects_invalid_bounds_versions_and_vectors(self):
        job = self.exchange().describe(self.model, self.photo_sha256)
        wire = analysis.encode_analysis(job, (1, 64, 32), [face_sample()])
        self.assertEqual(analysis.decode_analysis(job, wire)[0], (1, 64, 32))
        for value in (
            {**wire, "version": True},
            {**wire, "photo_sha256": "2" * 64},
            {**wire, "pipeline_signature": "2" * 64},
            {**wire, "geometry": [1, 1000000, 1000000]},
            {**wire, "faces": wire["faces"] * (analysis.MAX_FACES + 1)},
            {**wire, "faces": [{**wire["faces"][0], "bbox": [64, 1, 1, 1]}]},
        ):
            with self.subTest(value=value.keys()), self.assertRaises(transport.InvalidTransportData):
                analysis.decode_analysis(job, value)
        for vector in (np.zeros(512, dtype="<f4"), np.full(512, float("nan"), dtype="<f4")):
            encoded = base64.b64encode(vector.tobytes()).decode("ascii")
            with self.assertRaises(transport.InvalidTransportData):
                analysis.decode_analysis(job, {**wire, "faces": [{"bbox": [5, 5, 10, 12], "embedding": encoded}]})

    def test_model_fingerprint_is_path_independent_and_checks_weight_replacement(self):
        def fake_active_model(root):
            root.mkdir()
            models = {}
            assets = []
            for task in face_model.FACE_MODELS:
                path = root / f"{task}.onnx"
                path.write_bytes(task.encode())
                assets.append((task, path, face_model._weight_identity(path.stat())))
                models[task] = SimpleNamespace(
                    input_size=(112, 112), input_mean=127.5, input_std=128, det_thresh=0.5, nms_thresh=0.4,
                )
            instance = object.__new__(face_model.FaceModel)
            instance._lock = Lock()
            instance._active = (SimpleNamespace(models=models, _face_manager_weight_assets=tuple(assets)), "cpu")
            instance._shared_signature_cache = None
            return instance

        first = fake_active_model(self.share / "first")
        second = fake_active_model(self.share / "second")
        signature = first.shared_pipeline_signature()
        self.assertEqual(signature, second.shared_pipeline_signature())
        with patch.object(Path, "open", side_effect=AssertionError("cached fingerprint reread")):
            self.assertEqual(first.shared_pipeline_signature(), signature)
        (self.share / "first" / "detection.onnx").write_bytes(b"replacement")
        with self.assertRaisesRegex(RuntimeError, "changed"):
            first.shared_pipeline_signature()


if __name__ == "__main__":
    unittest.main()
