import os
from pathlib import Path
import tempfile
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
                    self.assertEqual(timer.call_args.kwargs["args"], (root,))
                finally:
                    budget._scheduled.discard(root)

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
                with patch.object(budget, "schedule_preview_cache_cleanup") as schedule:
                    budget._sweep(root)
                    schedule.assert_called_once_with(root)
            budget._last_completed.pop(root, None)
            budget._under_pressure.pop(root, None)


if __name__ == "__main__":
    unittest.main()
