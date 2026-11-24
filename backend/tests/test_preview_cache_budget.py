import os
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Event, Thread
import time
import unittest
from unittest.mock import patch

from backend.services import preview_cache_budget as budget


def write_preview(root, name, size, timestamp):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    os.utime(path, (timestamp, timestamp))
    return path


def update_in_another_process(data, limit):
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import json, sys; from backend.services import preview_cache_budget as b; "
            "b._schedule_locked = lambda *a, **k: None; "
            "b.set_preview_cache_limit_mib(json.loads(sys.argv[1]))",
            str(limit) if limit is not None else "null",
        ],
        cwd=Path(__file__).resolve().parents[2],
        env={**os.environ, "FACE_MANAGER_DATA_DIR": str(data)},
        check=True,
        timeout=10,
    )


class PreviewCacheBudgetTest(unittest.TestCase):
    def test_age_and_size_apply_across_both_preview_types(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "thumbnails"
            now = time.time()
            expired = write_preview(root, "faces/0000/1.jpg", 4, now - budget.MAX_PREVIEW_AGE_SECONDS - 1)
            older = write_preview(root, "images/0000/2-" + "a" * 20 + ".jpg", 5, now - 100)
            recent = write_preview(root, "faces/0000/3-" + "b" * 24 + ".jpg", 6, now - 10)
            incoming = write_preview(root, "images/0000/preview-a.tmp", 100, now - 100)
            original = write_preview(root.parent, "original.jpg", 100, now - 100)

            self.assertEqual(budget.prune_preview_cache(root, max_bytes=8, now=now), (2, 6))
            self.assertFalse(expired.exists())
            self.assertFalse(older.exists())
            self.assertTrue(recent.exists())
            self.assertTrue(incoming.exists())
            self.assertTrue(original.exists())

    def test_touch_and_recheck_preserve_a_preview_used_during_a_sweep(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "thumbnails"
            now = time.time()
            old = now - budget.MAX_PREVIEW_AGE_SECONDS - 50
            path = write_preview(root, "images/0000/2-" + "a" * 20 + ".jpg", 2, old)
            discovered = budget._managed_previews

            def enumerate_and_touch(cache_root):
                yield from discovered(cache_root)
                self.assertTrue(budget.preview_exists(path))

            with patch.object(budget, "_managed_previews", side_effect=enumerate_and_touch):
                self.assertEqual(budget.prune_preview_cache(root, max_bytes=0, now=now), (0, 2))
            self.assertGreater(path.stat().st_mtime, old)
            self.assertTrue(path.exists())

    def test_linked_files_and_nonmatching_shards_are_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "thumbnails"
            now = time.time()
            wrong_shard = write_preview(root, "faces/0001/1.jpg", 2, now)
            linked = root / "faces/0001/1001.jpg"
            alias = root / "faces/0001/1002.jpg"
            original = write_preview(root.parent, "original.jpg", 10, now)
            try:
                os.link(original, linked)
                alias.symlink_to(original)
            except OSError as exc:
                self.skipTest(f"File links unavailable: {exc}")

            self.assertEqual(budget.prune_preview_cache(root, max_bytes=0, now=now), (0, 0))
            self.assertTrue(linked.exists())
            self.assertTrue(alias.is_symlink())
            self.assertTrue(wrong_shard.exists())

    def test_read_reads_only_managed_regular_preview_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "thumbnails"
            path = write_preview(root, "faces/0000/1.jpg", 10, time.time())
            self.assertEqual(budget.read_preview_bytes(path, 4), b"xxxx")

    def test_cache_hit_and_write_directory_reject_a_redirected_shard(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "thumbnails"
            shard = root / "faces/0000"
            shard.parent.mkdir(parents=True)
            original = write_preview(root.parent, "photos/1.jpg", 4, time.time() - 10_000)
            old = original.stat().st_mtime_ns
            try:
                shard.symlink_to(original.parent, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"Directory links unavailable: {exc}")

            self.assertFalse(budget.preview_exists(shard / "1.jpg"))
            with self.assertRaises(OSError):
                budget.read_preview_bytes(shard / "1.jpg", 10)
            with self.assertRaises(OSError):
                with budget.preview_directory(shard / "2.jpg", create=True):
                    self.fail("A redirected cache must not accept writes")
            self.assertEqual(original.stat().st_mtime_ns, old)

    def test_successful_write_schedules_the_correct_cache_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "thumbnails"
            with patch.object(budget, "Timer") as timer:
                try:
                    budget.note_preview_written(root / "images/0000/1.jpg")
                    budget.note_preview_written(root / "faces/0000/1.jpg")
                    timer.assert_called_once()
                    self.assertEqual(timer.call_args.kwargs["args"][0], root)
                finally:
                    budget._scheduled.pop(root, None)

    @unittest.skipIf(os.name == "nt", "Directory descriptor test requires POSIX")
    def test_entry_swap_cannot_touch_a_linked_original(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "thumbnails"
            old = time.time() - 10_000
            preview = write_preview(root, "faces/0000/1.jpg", 5, old)
            original = write_preview(root.parent, "original.jpg", 10, old)
            timestamp = original.stat().st_mtime_ns
            open_file = os.open

            def open_after_swap(name, flags, *, dir_fd=None):
                if name == preview.name:
                    preview.unlink()
                    os.link(original, preview)
                return open_file(name, flags, dir_fd=dir_fd)

            with patch.object(budget.os, "open", side_effect=open_after_swap) as mocked:
                with patch.object(budget.os, "supports_dir_fd", os.supports_dir_fd | {mocked}):
                    budget.preview_exists(preview)
            self.assertEqual(original.stat().st_mtime_ns, timestamp)

    @unittest.skipIf(os.name == "nt", "Directory descriptor test requires POSIX")
    def test_directory_swap_cannot_redirect_cache_deletion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "thumbnails"
            now = time.time()
            preview = write_preview(root, "faces/0000/1.jpg", 5, now)
            original = write_preview(root.parent, "originals/1.jpg", 10, now)
            shard = preview.parent
            unlink = os.unlink

            def remove_after_swap(name, *, dir_fd):
                shard.rename(shard.with_name("old-shard"))
                shard.symlink_to(original.parent, target_is_directory=True)
                return unlink(name, dir_fd=dir_fd)

            with patch.object(budget.os, "unlink", side_effect=remove_after_swap) as mocked:
                with patch.object(budget.os, "supports_dir_fd", os.supports_dir_fd | {mocked}):
                    self.assertEqual(budget.prune_preview_cache(root, max_bytes=0), (1, 0))
            self.assertEqual(original.read_bytes(), b"x" * 10)

    def test_retry_is_scheduled_when_racing_reads_keep_cache_over_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "thumbnails"
            with patch.object(budget, "prune_preview_cache", return_value=(0, budget.MAX_PREVIEW_BYTES + 1)):
                with patch.object(budget, "_schedule_locked") as schedule:
                    budget._sweep(root)
                    schedule.assert_called_once_with(root)
            budget._last_completed.pop(root, None)
            budget._under_pressure.pop(root, None)

    def test_limit_persists_and_unlimited_skips_even_age_sweeps(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            root = data / "thumbnails"
            expired_at = time.time() - budget.MAX_PREVIEW_AGE_SECONDS - 1
            photo = write_preview(root, "images/0000/1-" + "a" * 20 + ".jpg", 4, expired_at)
            face = write_preview(root, "faces/0000/1.jpg", 4, expired_at)
            with patch.object(budget, "get_data_root", return_value=data), patch.object(budget, "Timer") as timer:
                self.assertEqual(budget.get_preview_cache_policy(), (768, False))
                budget.set_preview_cache_limit_mib(None)
                budget._policies.pop(root)
                self.assertEqual(budget.get_preview_cache_policy(), (None, False))
                budget.note_preview_written(photo)
                with patch.object(budget, "_managed_previews") as scan:
                    budget._sweep(root)
                    self.assertEqual(budget.prune_preview_cache(root, max_bytes=0), (0, 0))
                    scan.assert_not_called()
                timer.assert_not_called()
                self.assertTrue(photo.exists())
                self.assertTrue(face.exists())

                budget._last_completed[root] = time.monotonic()
                budget.set_preview_cache_limit_mib(128)
                timer.assert_called_once()
                self.assertEqual(timer.call_args.args[0], 0.0)
                budget._policies.pop(root)
                self.assertEqual(budget.get_preview_cache_policy(), (128, False))
                budget._sweep(*timer.call_args.kwargs["args"])
                self.assertFalse(photo.exists())
                self.assertFalse(face.exists())

    def test_unreadable_policy_suspends_cleanup_until_repaired(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            root = data / "thumbnails"
            policy = data / budget._POLICY_FILENAME
            policy.write_text("{invalid", encoding="utf-8")
            with patch.object(budget, "get_data_root", return_value=data):
                self.assertEqual(budget.get_preview_cache_policy(), (None, True))
                with patch.object(budget, "_managed_previews") as scan:
                    budget.prune_preview_cache(root)
                    scan.assert_not_called()
                budget.set_preview_cache_limit_mib(None)
                budget._policies.pop(root)
                self.assertEqual(budget.get_preview_cache_policy(), (None, False))

    def test_limit_change_supersedes_a_delayed_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            root = data / "thumbnails"
            budget._last_completed[root] = time.monotonic()
            with patch.object(budget, "get_data_root", return_value=data), patch.object(budget, "Timer") as timer:
                budget.schedule_preview_cache_cleanup(root)
                delayed = timer.call_args
                self.assertGreater(delayed.args[0], 0)
                budget.set_preview_cache_limit_mib(256)
                timer.return_value.cancel.assert_called_once()
                self.assertEqual(timer.call_args.args[0], 0)
                with patch.object(budget, "_managed_previews") as scan:
                    budget._sweep(*delayed.kwargs["args"])
                    scan.assert_not_called()
                budget.set_preview_cache_limit_mib(None)
                self.assertNotIn(root, budget._scheduled)

    def test_failed_persistence_retains_current_policy_and_pressure(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            root = data / "thumbnails"
            with patch.object(budget, "get_data_root", return_value=data), patch.object(budget, "Timer"):
                budget.set_preview_cache_limit_mib(768)
                budget._under_pressure[root] = True
                try:
                    with patch.object(budget.os, "replace", side_effect=OSError("read only")):
                        with self.assertRaises(OSError):
                            budget.set_preview_cache_limit_mib(None)
                    self.assertEqual(budget.get_preview_cache_policy(), (768, False))
                    self.assertFalse(budget.cache_warmup_has_room())
                    budget._policies.pop(root)
                    self.assertEqual(budget.get_preview_cache_policy(), (768, False))
                    self.assertEqual(
                        {path.name for path in data.iterdir()},
                        {budget._POLICY_FILENAME, budget._POLICY_LOCK_FILENAME},
                    )
                finally:
                    budget._under_pressure.pop(root, None)
                    budget._scheduled.pop(root, None)

    def test_policy_change_stops_running_sweep_before_its_next_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            root = data / "thumbnails"
            old = time.time() - budget.MAX_PREVIEW_AGE_SECONDS - 1
            previews = [write_preview(root, f"faces/0000/{i}.jpg", 4, old) for i in (1, 2)]
            scanned, release = Event(), Event()
            discover = budget._managed_previews

            def managed_files(cache_root):
                yield from discover(cache_root)
                scanned.set()
                if not release.wait(15):
                    raise TimeoutError("test did not release the sweep")

            with (
                patch.object(budget, "get_data_root", return_value=data),
                patch.object(budget, "_managed_previews", side_effect=managed_files),
                patch.object(budget, "_unlink_unchanged", wraps=budget._unlink_unchanged) as delete,
            ):
                worker = Thread(target=budget._sweep, args=(root,))
                worker.start()
                try:
                    self.assertTrue(scanned.wait(1))
                    update_in_another_process(data, None)
                finally:
                    release.set()
                    worker.join(2)
                self.assertFalse(worker.is_alive())
                delete.assert_not_called()
                self.assertTrue(all(path.exists() for path in previews))
                self.assertTrue(budget.cache_warmup_has_room())
                self.assertNotIn(root, budget._scheduled)

    def test_local_write_observes_finite_policy_saved_by_another_process(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            root = data / "thumbnails"
            with patch.object(budget, "get_data_root", return_value=data), patch.object(budget, "Timer") as timer:
                budget.set_preview_cache_limit_mib(None)
                self.assertEqual(budget.get_preview_cache_policy(), (None, False))
                update_in_another_process(data, 512)
                budget.note_preview_written(root / "faces/0000/1.jpg")
                timer.assert_called_once()
                self.assertEqual(timer.call_args.args[0], 0)
                self.assertEqual(budget.get_preview_cache_policy(), (512, False))
                budget._scheduled.pop(root, None)


if __name__ == "__main__":
    unittest.main()
