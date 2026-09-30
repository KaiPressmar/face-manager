import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.services.update_manager import (
    UpdateError,
    UpdateManager,
    parse_latest_release,
    parse_semver,
)


def release_payload(version="1.2.0", variant="cpu", content=b"installer"):
    suffix = "-GPU" if variant == "gpu" else ""
    installer = f"FaceManager-Setup{suffix}-{version}.exe"
    checksum = hashlib.sha256(content).hexdigest()
    base = f"https://github.com/KaiPressmar/face-manager/releases/download/v{version}"
    return {
        "tag_name": f"v{version}",
        "html_url": f"https://github.com/KaiPressmar/face-manager/releases/tag/v{version}",
        "published_at": "2026-07-20T12:00:00Z",
        "draft": False,
        "prerelease": False,
        "body": "# Face Manager\n\n## Neu\n\n- Ein sichtbares Update.\n",
        "assets": [
            {
                "name": installer,
                "browser_download_url": f"{base}/{installer}",
                "digest": f"sha256:{checksum}",
            },
            {
                "name": f"{installer}.sha256",
                "browser_download_url": f"{base}/{installer}.sha256",
            },
        ],
    }


class FakeResponse(io.BytesIO):
    def __init__(self, content: bytes, url: str, content_length: bool = True):
        super().__init__(content)
        self._url = url
        self.headers = {"Content-Length": str(len(content))} if content_length else {}

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class UpdateMetadataTest(unittest.TestCase):
    def test_semver_comparison_is_numeric(self):
        self.assertGreater(parse_semver("v1.10.0"), parse_semver("1.9.9"))

    def test_release_selects_exact_gpu_installer_and_high_level_notes(self):
        result, candidate = parse_latest_release(
            release_payload(variant="gpu"), "1.1.9", "gpu"
        )

        self.assertTrue(result["update_available"])
        self.assertTrue(result["download_available"])
        self.assertEqual(result["installer_name"], "FaceManager-Setup-GPU-1.2.0.exe")
        self.assertEqual(
            result["sections"],
            [{"title": "Neu", "items": ["Ein sichtbares Update."]}],
        )
        self.assertEqual(candidate["version"], "1.2.0")

    def test_release_without_matching_checksum_is_not_downloadable(self):
        payload = release_payload()
        payload["assets"] = payload["assets"][:1]

        result, candidate = parse_latest_release(payload, "1.1.0", "cpu")

        self.assertTrue(result["update_available"])
        self.assertFalse(result["download_available"])
        self.assertIsNone(candidate)

    def test_prerelease_is_rejected(self):
        payload = release_payload()
        payload["prerelease"] = True

        with self.assertRaises(UpdateError):
            parse_latest_release(payload, "1.1.0", "cpu")

    def test_release_request_is_cached_for_one_hour(self):
        manager = UpdateManager()
        response = FakeResponse(
            json.dumps(release_payload()).encode("utf-8"),
            "https://api.github.com/repos/KaiPressmar/face-manager/releases/latest",
        )
        with patch.object(manager, "_request", return_value=response) as request, patch(
            "backend.services.update_manager.time.monotonic",
            side_effect=[100.0, 3699.0],
        ):
            first = manager.check("1.1.0", "cpu")
            second = manager.check("1.1.0", "cpu")

        request.assert_called_once()
        self.assertEqual(first, second)


class UpdateDownloadTest(unittest.TestCase):
    def test_download_is_written_only_after_matching_checksum(self):
        content = b"verified installer bytes"
        payload = release_payload(content=content)
        result, candidate = parse_latest_release(payload, "1.1.0", "cpu")
        manager = UpdateManager()
        manager._cached_result = result
        manager._candidate = candidate
        checksum = hashlib.sha256(content).hexdigest()

        responses = [
            FakeResponse(
                f"{checksum}  {candidate['installer_name']}\n".encode("ascii"),
                "https://objects.githubusercontent.com/checksum",
            ),
            FakeResponse(
                content,
                "https://objects.githubusercontent.com/installer",
            ),
        ]
        with tempfile.TemporaryDirectory() as temporary_directory, patch.object(
            manager, "_request", side_effect=responses
        ):
            manager._download_state = {"status": "downloading"}
            manager._download(candidate, Path(temporary_directory))
            state = manager.download_state()

            self.assertEqual(state["status"], "ready")
            self.assertEqual(state["sha256"], checksum)
            self.assertEqual(
                (Path(temporary_directory) / candidate["installer_name"]).read_bytes(),
                content,
            )

    def test_checksum_mismatch_never_publishes_installer(self):
        content = b"tampered installer"
        payload = release_payload(content=b"expected installer")
        result, candidate = parse_latest_release(payload, "1.1.0", "cpu")
        manager = UpdateManager()
        manager._cached_result = result
        manager._candidate = candidate
        expected = hashlib.sha256(b"expected installer").hexdigest()
        responses = [
            FakeResponse(
                f"{expected}  {candidate['installer_name']}\n".encode("ascii"),
                "https://objects.githubusercontent.com/checksum",
            ),
            FakeResponse(content, "https://objects.githubusercontent.com/installer"),
        ]

        with tempfile.TemporaryDirectory() as temporary_directory, patch.object(
            manager, "_request", side_effect=responses
        ):
            manager._download_state = {"status": "downloading"}
            manager._download(candidate, Path(temporary_directory))

            self.assertEqual(manager.download_state()["status"], "error")
            self.assertFalse(
                (Path(temporary_directory) / candidate["installer_name"]).exists()
            )


if __name__ == "__main__":
    unittest.main()
