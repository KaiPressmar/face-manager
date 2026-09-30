import unittest
from unittest.mock import patch

from backend.services.cache import AppCache


class AppCacheTest(unittest.TestCase):
    def test_invalidate_tags_removes_only_matching_entries(self):
        cache = AppCache(default_ttl_seconds=60, max_bytes=1024 * 1024, max_entries=10)
        cache.set(("images", 1), {"id": 1}, tags={"images"})
        cache.set(("settings",), {"value": 1}, tags={"settings"})

        removed = cache.invalidate_tags("images")

        self.assertEqual(removed, 1)
        self.assertIsNone(cache.get(("images", 1)))
        self.assertEqual(cache.get(("settings",)), {"value": 1})

    def test_large_entries_are_rejected_when_above_entry_budget(self):
        cache = AppCache(
            default_ttl_seconds=60,
            max_bytes=1024 * 1024,
            max_entries=10,
            max_entry_bytes=8,
        )

        cache.set(("oversized",), "this entry is too large", tags={"images"})

        self.assertIsNone(cache.get(("oversized",)))
        self.assertEqual(cache.get_stats().rejected_entries, 1)

    def test_cache_evicts_oldest_stale_entries_to_stay_within_budget(self):
        cache = AppCache(
            default_ttl_seconds=60,
            max_bytes=90,
            max_entries=10,
            max_entry_bytes=120,
        )

        cache.set(("first",), "a" * 40, tags={"images"})
        cache.set(("second",), "b" * 40, tags={"images"})
        cache.get(("first",))
        cache.set(("third",), "c" * 40, tags={"images"})

        self.assertIsNone(cache.get(("second",)))
        self.assertEqual(cache.get(("first",)), "a" * 40)
        self.assertEqual(cache.get(("third",)), "c" * 40)

    def test_eviction_uses_access_order_when_clock_advances_during_scoring(self):
        cache = AppCache(default_ttl_seconds=60, max_bytes=100, max_entries=2)
        cache.set("older", "a", size_bytes=1)
        cache.set("newer", "b", size_bytes=1)
        cache._entries["older"].last_accessed_at = 100.0
        cache._entries["newer"].last_accessed_at = 101.0
        cache.max_entries = 1

        with patch(
            "backend.services.cache.time.monotonic",
            side_effect=[1000.0, 1000.0, 2000.0, 2000.0],
        ):
            cache._evict_if_needed_locked()

        self.assertEqual(cache.get("newer"), "b")
        self.assertIsNone(cache.get("older"))
        self.assertEqual(cache.get_stats().evictions, 1)

    def test_eviction_ties_prefer_larger_then_older_entry(self):
        for first_size, second_size, first_created, second_created, survivor in (
            (2, 1, 100.0, 100.0, "second"),
            (1, 2, 100.0, 100.0, "first"),
            (1, 1, 100.0, 101.0, "second"),
            (1, 1, 101.0, 100.0, "first"),
        ):
            with self.subTest(
                first_size=first_size,
                second_size=second_size,
                first_created=first_created,
            ):
                cache = AppCache(default_ttl_seconds=60, max_bytes=100, max_entries=2)
                cache.set("first", "a", size_bytes=first_size)
                cache.set("second", "b", size_bytes=second_size)
                cache._entries["first"].last_accessed_at = 200.0
                cache._entries["second"].last_accessed_at = 200.0
                cache._entries["first"].created_at = first_created
                cache._entries["second"].created_at = second_created
                cache.max_entries = 1

                cache._evict_if_needed_locked()

                self.assertEqual(cache.get(survivor), "a" if survivor == "first" else "b")
                self.assertIsNone(cache.get("second" if survivor == "first" else "first"))


if __name__ == "__main__":
    unittest.main()
