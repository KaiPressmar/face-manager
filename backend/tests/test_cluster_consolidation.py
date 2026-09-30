import unittest

import numpy as np

from backend.models.clustering import consolidate_small_clusters


class ClusterConsolidationTest(unittest.TestCase):
    @staticmethod
    def _vector(angle_degrees: float) -> np.ndarray:
        angle = np.deg2rad(angle_degrees)
        return np.array([np.cos(angle), np.sin(angle)], dtype=np.float32)

    def test_singleton_joins_larger_cluster_with_neighbor_consensus(self):
        embeddings = np.vstack(
            [self._vector(0), self._vector(5), self._vector(10), self._vector(18)]
        )

        result = consolidate_small_clusters(
            embeddings,
            np.array([1, 1, 1, 2]),
            distance_threshold=0.02,
        )

        self.assertEqual(result.tolist(), [1, 1, 1, 1])

    def test_two_face_cluster_requires_both_members_to_agree(self):
        embeddings = np.vstack(
            [
                self._vector(0),
                self._vector(5),
                self._vector(10),
                self._vector(18),
                self._vector(80),
            ]
        )

        result = consolidate_small_clusters(
            embeddings,
            np.array([1, 1, 1, 2, 2]),
            distance_threshold=0.02,
        )

        self.assertEqual(result.tolist(), [1, 1, 1, 2, 2])

    def test_ambiguous_singleton_is_not_merged(self):
        embeddings = np.vstack(
            [
                self._vector(0),
                self._vector(4),
                self._vector(8),
                self._vector(32),
                self._vector(36),
                self._vector(40),
                self._vector(20),
            ]
        )

        result = consolidate_small_clusters(
            embeddings,
            np.array([1, 1, 1, 2, 2, 2, 3]),
            distance_threshold=0.08,
        )

        self.assertEqual(result[-1], 3)

    def test_immovable_existing_cluster_is_not_changed(self):
        embeddings = np.vstack(
            [self._vector(0), self._vector(5), self._vector(10), self._vector(18)]
        )

        result = consolidate_small_clusters(
            embeddings,
            np.array([1, 1, 1, 2]),
            distance_threshold=0.02,
            movable_mask=np.array([False, False, False, False]),
        )

        self.assertEqual(result.tolist(), [1, 1, 1, 2])


if __name__ == "__main__":
    unittest.main()
