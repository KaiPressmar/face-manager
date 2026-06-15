import unittest

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


if __name__ == "__main__":
    unittest.main()
