import hashlib
import io
import json
import os
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from backend.services.update_manager import (
    UpdateError,
    UpdateManager,
    _UpdateRedirectHandler,
    parse_latest_release,
    parse_semver,
)


def release_payload(version="1.2.0", variant="cpu", content=b"installer"):
    suffix = "-GPU" if variant == "gpu" else ""
    installer = f"face-manager_{version}_amd64.deb" if variant == "linux" else f"FaceManager-Setup{suffix}-{version}.exe"
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

    def test_desktop_channels_select_the_packages_for_each_os(self):
        linux_payload = release_payload(variant="linux")
        windows_payload = release_payload(variant="windows")
        combined = {**linux_payload, "assets": linux_payload["assets"] + windows_payload["assets"]}
        for variant, filename in (
            ("windows", "FaceManager-Setup-1.2.0.exe"),
            ("linux", "face-manager_1.2.0_amd64.deb"),
        ):
            with self.subTest(variant=variant):
                result, candidate = parse_latest_release(combined, "1.1.0", variant)
                self.assertEqual(result["installer_name"], filename)
                self.assertTrue(candidate["checksum_url"].endswith(f"/{filename}.sha256"))

    def test_prerelease_is_rejected(self):
        payload = release_payload()
        payload["prerelease"] = True

        with self.assertRaises(UpdateError):
            parse_latest_release(payload, "1.1.0", "cpu")

    def test_release_asset_is_bound_to_the_release_tag(self):
        payload = release_payload()
        asset = payload["assets"][0]
        asset["browser_download_url"] = asset["browser_download_url"].replace("/v1.2.0/", "/v1.3.0/")
        with self.assertRaises(UpdateError):
            parse_latest_release(payload, "1.1.0", "cpu")

    def test_untrusted_redirect_is_rejected_before_the_request(self):
        request = urllib.request.Request("https://github.com/")
        handler = _UpdateRedirectHandler()
        with self.assertRaises(UpdateError):
            handler.redirect_request(request, None, 302, "", {}, "https://example.invalid/")
        cdn = "https://release-assets.githubusercontent.com/update"
        self.assertEqual(handler.redirect_request(request, None, 302, "", {}, cdn).full_url, cdn)

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
    def test_activity_reports_bounded_progress_and_verified_completion(self):
        snapshots = []
        manager = UpdateManager(snapshots.append)
        content = b"a" * (2 * 1024 * 1024)
        manager._cached_result, manager._candidate = parse_latest_release(
            release_payload(content=content), "1.1.0", "cpu"
        )
        checksum = hashlib.sha256(content).hexdigest()

        def immediate_worker(*, target, args, **_options):
            class Worker:
                def start(self):
                    target(*args)

            return Worker()

        with tempfile.TemporaryDirectory() as folder, patch(
            "backend.services.update_manager.threading.Thread", side_effect=immediate_worker
        ), patch.object(manager, "_fetch_checksum", return_value=checksum), patch.object(
            manager, "_request", return_value=FakeResponse(content, "https://objects.githubusercontent.com/installer")
        ), patch("backend.services.update_manager.time.monotonic", side_effect=[10.0, 10.1, 10.8, 10.9, 11.0]):
            self.assertEqual(manager.start_download("1.2.0", Path(folder))["status"], "ready")

        self.assertEqual([snapshot["status"] for snapshot in snapshots], ["downloading", "downloading", "downloading", "ready"])
        self.assertEqual([snapshot["downloaded_bytes"] for snapshot in snapshots], [0, 0, len(content) // 2, len(content)])
        self.assertEqual(snapshots[-1]["total_bytes"], len(content))
        self.assertNotIn("path", snapshots[-1])
        self.assertNotIn("sha256", snapshots[-1])

    def test_activity_error_hides_private_download_error_details(self):
        snapshots = []
        manager = UpdateManager(snapshots.append)
        _, candidate = parse_latest_release(release_payload(), "1.1.0", "cpu")
        manager._set_download_error(candidate, OSError("private folder name"))

        self.assertEqual(snapshots, [{"status": "error", "version": "1.2.0"}])
        self.assertIn("private folder name", manager.download_state()["error"])

    def test_directory_failure_leaves_a_retryable_error(self):
        manager = UpdateManager()
        _, candidate = parse_latest_release(release_payload(), "1.1.0", "cpu")

        with tempfile.TemporaryDirectory() as temporary_directory:
            blocked = Path(temporary_directory) / "updates"
            blocked.write_text("not a directory", encoding="utf-8")
            manager._download_state = {"status": "downloading"}
            manager._download(candidate, blocked, "1.1.0")

        self.assertEqual(manager.download_state()["status"], "error")

    def test_cleanup_failure_preserves_the_download_error(self):
        manager = UpdateManager()
        _, candidate = parse_latest_release(release_payload(), "1.1.0", "cpu")
        response = FakeResponse(b"installer", "https://objects.githubusercontent.com/installer")
        response.read = unittest.mock.Mock(side_effect=OSError("Connection interrupted"))

        with tempfile.TemporaryDirectory() as temporary_directory, patch.object(
            manager, "_fetch_checksum", return_value="unused"
        ), patch.object(
            manager, "_request", return_value=response
        ), patch.object(Path, "unlink", side_effect=PermissionError("Partial file locked")):
            manager._download_state = {"status": "downloading"}
            manager._download(candidate, Path(temporary_directory), "1.1.0")

        self.assertEqual(manager.download_state()["status"], "error")
        self.assertEqual(manager.download_state()["error"], "Connection interrupted")

    def test_thread_start_failure_allows_retry(self):
        manager = UpdateManager()
        manager._cached_result, manager._candidate = parse_latest_release(
            release_payload(), "1.1.0", "cpu"
        )
        version = str(manager._cached_result["latest_version"])

        with tempfile.TemporaryDirectory() as temporary_directory, patch(
            "backend.services.update_manager.threading.Thread"
        ) as worker:
            worker.return_value.start.side_effect = [RuntimeError("Cannot start worker"), None]
            target = Path(temporary_directory)
            failed = manager.start_download(version, target)
            retried = manager.start_download(version, target)

        self.assertEqual(failed["status"], "error")
        self.assertEqual(retried["status"], "downloading")
        self.assertEqual(worker.return_value.start.call_count, 2)

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
            manager._download(candidate, Path(temporary_directory), "1.1.0")
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
            manager._download(candidate, Path(temporary_directory), "1.1.0")

            self.assertEqual(manager.download_state()["status"], "error")
            self.assertFalse(
                (Path(temporary_directory) / candidate["installer_name"]).exists()
            )

    def test_cached_installer_is_reused_only_if_it_matches_the_published_hash(self):
        manager = UpdateManager()
        content = b"installer"
        _, candidate = parse_latest_release(release_payload(content=content), "1.1.0", "cpu")
        expected = hashlib.sha256(content).hexdigest()
        with tempfile.TemporaryDirectory() as temporary_directory, patch.object(
            manager, "_fetch_checksum", return_value=expected,
        ), patch.object(manager, "_request") as request:
            directory = Path(temporary_directory)
            installer = directory / candidate["installer_name"]
            installer.write_bytes(content)
            manager._download(candidate, directory, "1.1.0")
            request.assert_not_called()
            self.assertEqual(manager.download_state()["status"], "ready")
            installer.write_bytes(b"corrupt")
            request.return_value = FakeResponse(content, "https://release-assets.githubusercontent.com/installer")
            manager._download(candidate, directory, "1.1.0")
            request.assert_called_once()
            self.assertEqual(installer.read_bytes(), content)
            self.assertEqual(manager.download_state()["status"], "ready")

    def test_checksum_must_apply_to_exactly_the_expected_installer(self):
        manager = UpdateManager()
        _, candidate = parse_latest_release(release_payload(), "1.1.0", "cpu")
        digest = candidate["installer_digest"].removeprefix("sha256:")
        contents = f"{digest}  another.exe\n{digest}  {candidate['installer_name']}\n"
        response = FakeResponse(contents.encode("ascii"), "https://objects.githubusercontent.com/checksum")
        with patch.object(manager, "_request", return_value=response), self.assertRaises(UpdateError):
            manager._fetch_checksum(candidate)

    def test_installing_changed_file_clears_ready_state_and_allows_retry(self):
        manager = UpdateManager()
        content = b"installer"
        manager._cached_result, candidate = parse_latest_release(
            release_payload(content=content), "1.1.0", "cpu",
        )
        manager._candidate = candidate
        expected = hashlib.sha256(content).hexdigest()
        with tempfile.TemporaryDirectory() as temporary_directory, patch.object(
            manager, "can_install", return_value=True,
        ), patch("backend.services.update_manager.subprocess.Popen") as launch:
            installer = Path(temporary_directory) / candidate["installer_name"]
            installer.write_bytes(b"tampered!")
            manager._set_download_ready(candidate, installer, len(content), expected)
            with self.assertRaisesRegex(UpdateError, "verändert"):
                manager.launch_downloaded_installer(candidate["version"])
            launch.assert_not_called()
            self.assertEqual(manager.download_state()["status"], "error")


class UpdateCacheTest(unittest.TestCase):
    def test_cleanup_keeps_latest_pending_per_build_and_protects_recent_files(self):
        manager = UpdateManager()
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            names_to_remove = {
                "FaceManager-Setup-1.0.0.exe",
                "FaceManager-Setup-1.2.0.exe",
                "FaceManager-Android-1.1.0.apk",
                "face-manager_1.1.0_amd64.deb",
                "FaceManager-Setup-1.0.0.exe.part",
                "FaceManager-Setup-1.3.0.exe.abc_1234.part",
            }
            names_to_keep = {
                "FaceManager-Setup-1.3.0.exe",
                "FaceManager-Setup-GPU-1.2.0.exe",
                "FaceManager-Android-1.3.0.apk",
                "face-manager_1.2.0_amd64.deb",
                "OtherApp-Setup-1.0.0.exe",
                "FaceManager-Setup-1.0.0.exe.bak",
            }
            recent_files = {"FaceManager-Setup-1.1.0.exe", "FaceManager-Setup-1.4.0.exe.abc_1234.part"}
            old = time.time() - 48 * 60 * 60
            for name in names_to_keep | names_to_remove | recent_files:
                path = directory / name
                path.write_bytes(b"file")
                if name not in recent_files:
                    os.utime(path, (old, old))
            unrelated_directory = directory / "FaceManager-Setup-GPU-1.0.0.exe"
            unrelated_directory.mkdir()
            manager.cleanup_cache(directory, "1.1.0")
            self.assertEqual(
                {path.name for path in directory.iterdir()},
                names_to_keep | recent_files | {unrelated_directory.name},
            )

    def test_cleanup_ignores_linked_cache_directory_and_linked_installers(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            directory = root / "regular"
            directory.mkdir()
            installer = directory / "FaceManager-Setup-1.0.0.exe"
            installer.write_bytes(b"outside")
            old = time.time() - 48 * 60 * 60
            os.utime(installer, (old, old))
            linked_cache = root / "updates"
            try:
                linked_cache.symlink_to(directory, target_is_directory=True)
                linked_file = directory / "FaceManager-Setup-0.5.0.exe"
                linked_file.symlink_to(installer)
            except OSError as exc:
                self.skipTest(f"Symbolic links unavailable: {exc}")
            manager = UpdateManager()
            manager.cleanup_cache(linked_cache, "1.1.0")
            self.assertTrue(installer.is_file())
            manager.cleanup_cache(directory, "1.1.0")
            self.assertTrue(linked_file.is_symlink())


if __name__ == "__main__":
    unittest.main()
