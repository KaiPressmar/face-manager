import unittest

from backend.services.face_thumbnail_warmup import FaceThumbnailWarmupQueue
from backend.services.task_control import BackgroundTaskControl


class BackgroundTaskControlTest(unittest.TestCase):
    def test_cancel_releases_pause_checkpoint(self):
        control = BackgroundTaskControl()
        control.pause()
        control.set()

        self.assertTrue(control.wait_if_paused())
        self.assertTrue(control.is_set())


class ThumbnailWarmupControlTest(unittest.TestCase):
    def setUp(self):
        self.queue = FaceThumbnailWarmupQueue(is_idle=lambda: False)

    def test_pause_resume_cancel_and_dismiss(self):
        self.assertEqual(self.queue.pause()["status"], "paused")
        self.assertTrue(self.queue.snapshot()["task"]["user_paused"])
        self.assertEqual(self.queue.resume()["status"], "idle")
        self.assertEqual(self.queue.cancel()["status"], "cancelled")
        self.assertTrue(self.queue.dismiss_history())
        self.assertIsNone(self.queue.snapshot()["task"])


if __name__ == "__main__":
    unittest.main()
