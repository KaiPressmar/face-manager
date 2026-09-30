import unittest
from unittest.mock import Mock

from backend.services.idle_recluster import IdleReclusterScheduler


class IdleReclusterSchedulerTest(unittest.TestCase):
    def test_waits_until_idle_and_coalesces_changes(self):
        idle = Mock(return_value=False)
        schedule = Mock()
        scheduler = IdleReclusterScheduler(
            idle,
            schedule,
            debounce_seconds=60,
            retry_seconds=60,
        )
        self.addCleanup(scheduler.stop)

        scheduler.mark_dirty("assign_person")
        scheduler.mark_dirty("remove_face")
        self.assertFalse(scheduler.check_now())
        schedule.assert_not_called()

        idle.return_value = True
        self.assertTrue(scheduler.check_now())
        schedule.assert_called_once_with("idle:remove_face")
        self.assertFalse(scheduler.check_now())

    def test_clear_prevents_pending_automatic_run(self):
        schedule = Mock()
        scheduler = IdleReclusterScheduler(
            lambda: True,
            schedule,
            debounce_seconds=60,
        )
        self.addCleanup(scheduler.stop)

        scheduler.mark_dirty("assign_person")
        scheduler.clear()

        self.assertFalse(scheduler.check_now())
        schedule.assert_not_called()

    def test_retries_when_background_race_prevents_scheduling(self):
        schedule = Mock(return_value=None)
        scheduler = IdleReclusterScheduler(
            lambda: True,
            schedule,
            debounce_seconds=60,
            retry_seconds=60,
        )
        self.addCleanup(scheduler.stop)

        scheduler.mark_dirty("assign_person")

        self.assertFalse(scheduler.check_now())
        self.assertFalse(scheduler.check_now())
        self.assertEqual(schedule.call_count, 2)


if __name__ == "__main__":
    unittest.main()
