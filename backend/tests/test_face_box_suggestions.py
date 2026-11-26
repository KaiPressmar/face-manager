import hashlib
import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from backend.models import face_model
from backend.services import face_box_suggestions as service


class FaceBoxSuggestionTests(unittest.TestCase):
    def test_upright_boxes_and_existing_raw_faces_use_the_same_orientation(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "rotated.jpg"
            with Image.new("RGB", (160, 80), "white") as image:
                exif = image.getexif()
                exif[274] = 6
                image.save(source, exif=exif)
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            mismatch = Path(folder) / "mismatch.jpg"
            mismatch.write_bytes(source.read_bytes() + b"changed")

            def find(pixels):
                self.assertEqual(pixels.shape, (160, 80, 3))
                return np.array([
                    [40, 10, 65, 40, .91],    # Already named
                    [20, 100, 45, 125, .88],  # Already dismissed
                    [9, 55, 31, 82, .43],
                    [-4, 130, 22, 158, .42],  # Visible part of an edge box
                    [2, 4, 8, 11, .99],      # Too small
                    [3, 84, 26, 100, .25],   # Too weak
                    [10, 56, 30, 81, .40],   # Duplicate of the accepted hint
                    [-36, 40, 16, 60, .8],   # Mostly outside the photo
                    [np.nan, 0, 20, 21, .9],
                ], dtype=np.float32)

            model = SimpleNamespace(
                suggest_face_detections=Mock(side_effect=find),
                detect_and_embed=Mock(side_effect=AssertionError("Recognition must not run")),
            )
            result = service.suggest_face_boxes(
                [str(Path(folder) / "offline.jpg"), str(mismatch), str(source)], digest,
                [(10, 15, 30, 25), (100, 35, 25, 25), (None, 0, 10, 10)], lambda: model,
            )
            self.assertEqual(json.loads(json.dumps(result)), [
                {"x": 9 / 80, "y": 55 / 160, "width": 22 / 80, "height": 27 / 160},
                {"x": 0., "y": 130 / 160, "width": 22 / 80, "height": 28 / 160},
            ])
            model.suggest_face_detections.assert_called_once()
            model.detect_and_embed.assert_not_called()

    def test_legacy_mirror_exclusion_and_confidence_order_with_a_cap(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "mirror.jpg"
            with Image.new("RGB", (600, 400), "white") as image:
                exif = image.getexif()
                exif[274] = 2
                image.save(source, exif=exif)
            chosen = [
                [30 * index, 100, 30 * index + 20, 122, .37 + index / 100]
                for index in range(12)
            ]
            chosen.extend(([540, 20, 580, 62, .95], [350, 125, 374, 210, .85]))
            model = SimpleNamespace(suggest_face_detections=lambda _: np.array(chosen))
            result = service.suggest_face_boxes([str(source)], None, [(20, 20, 40, 42)], lambda: model)
            self.assertEqual(len(result), 8)
            self.assertEqual([box["x"] for box in result], [30 * index / 600 for index in reversed(range(4, 12))])

    def test_busy_budget_does_not_wait_for_an_import(self):
        @contextmanager
        def occupied():
            yield False

        get_model = Mock()
        pixels = np.empty((160, 160, 3), dtype=np.uint8)
        with (
            patch.object(service, "_read_photo", return_value=(pixels, (1, 160, 160))),
            patch.object(service, "try_face_processing_slot", occupied),
            self.assertRaises(service.FaceBoxSuggestionBusy),
        ):
            service.suggest_face_boxes(["photo.jpg"], "hash", [], get_model)
        get_model.assert_not_called()

    def test_request_gate_rejects_another_suggestion_before_opening_its_photo(self):
        with service._SUGGESTION_GATE, patch.object(service, "_read_photo") as read_photo:
            with self.assertRaises(service.FaceBoxSuggestionBusy):
                service.suggest_face_boxes(["photo.jpg"], "hash", [], Mock())
        read_photo.assert_not_called()

    def test_slow_source_does_not_start_inference_after_request_deadline(self):
        pixels = np.empty((160, 160, 3), dtype=np.uint8)
        get_model = Mock()
        with (
            patch.object(service, "monotonic", side_effect=[0, 1, 8]),
            patch.object(service, "_read_photo", return_value=(pixels, (1, 160, 160))),
            self.assertRaises(service.FaceBoxSuggestionBusy),
        ):
            service.suggest_face_boxes(["slow.jpg"], "hash", [], get_model)
        get_model.assert_not_called()

    def test_file_cap_and_invalid_photo_never_initialize_detector(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "oversized.jpg"
            source.write_bytes(b"x" * 512)
            get_model = Mock()
            with patch.object(service, "_MAX_PHOTO_BYTES", 128):
                with self.assertRaises(service.FaceBoxSuggestionPhotoUnavailable):
                    service.suggest_face_boxes([str(source)], None, [], get_model)
                with self.assertRaises(service.FaceBoxSuggestionPhotoUnavailable):
                    service.suggest_face_boxes([str(source)], hashlib.sha256(source.read_bytes()).hexdigest(), [], get_model)
            get_model.assert_not_called()

    def test_android_without_separate_detector_still_uses_manual_flow(self):
        with patch.dict(os.environ, {"FACE_MANAGER_PLATFORM": "android"}), patch.object(service, "_read_photo") as read:
            with self.assertRaises(service.FaceBoxSuggestionUnavailable):
                service.suggest_face_boxes(["unused.jpg"], None, [], Mock())
        read.assert_not_called()


class IsolatedDetectorTests(unittest.TestCase):
    def test_suggestion_holds_model_lock_without_modifying_prepared_detector(self):
        class Detector:
            det_thresh = .5
            center_cache = {"ordinary": object()}
            observed = []

            def detect(self, image):
                self.observed.append((self.det_thresh, dict(self.center_cache)))
                self.center_cache["suggestions"] = object()
                return np.array([[4., 6., 26., 32., .41]]), None

        detector = Detector()
        application = SimpleNamespace(
            det_model=detector, get=Mock(side_effect=AssertionError("Recognition must not run")),
        )
        model = face_model.FaceModel.__new__(face_model.FaceModel)
        model._active = (application, face_model.CPU_PROVIDER)
        model._lock = Lock()
        image = np.zeros((48, 48, 3), dtype=np.uint8)
        self.assertEqual(model.suggest_face_detections(image)[0, 4], .41)
        self.assertEqual(detector.det_thresh, .5)
        self.assertEqual(list(detector.center_cache), ["ordinary"])
        self.assertEqual(detector.observed, [(face_model.SUGGESTION_SCORE_THRESHOLD, {})])
        application.get.assert_not_called()

        with model._lock:
            with self.assertRaises(face_model.FaceDetectionBusy):
                model.suggest_face_detections(image)


if __name__ == "__main__":
    unittest.main()
