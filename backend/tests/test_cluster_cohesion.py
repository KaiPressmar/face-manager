import unittest

import numpy as np

from backend.models.clustering import FaceClustering, split_heterogeneous_clusters


class ClusterCohesionTest(unittest.TestCase):
    @staticmethod
    def _vector(angle_degrees: float) -> np.ndarray:
        angle = np.deg2rad(angle_degrees)
        return np.array([np.cos(angle), np.sin(angle)], dtype=np.float32)

    def test_gradual_similarity_chain_is_split(self):
        clusterer = FaceClustering(dim=2)
        assigned = []
        for angle in [0, 30, 60, 90]:
            cluster_ids, _ = clusterer.add_and_assign(
                np.vstack([self._vector(angle)]),
                distance_threshold=0.15,
            )
            assigned.append(int(cluster_ids[0]))

        self.assertEqual(assigned[0], assigned[1])
        self.assertNotEqual(assigned[1], assigned[2])
        self.assertEqual(assigned[2], assigned[3])

    def test_compact_cluster_still_grows_normally(self):
        clusterer = FaceClustering(dim=2)
        assigned = []
        for angle in [0, 4, 8, 12, 16]:
            cluster_ids, _ = clusterer.add_and_assign(
                np.vstack([self._vector(angle)]),
                distance_threshold=0.05,
            )
            assigned.append(int(cluster_ids[0]))

        self.assertEqual(len(set(assigned)), 1)

    def test_loaded_heterogeneous_cluster_rejects_distant_extension(self):
        clusterer = FaceClustering(dim=2)
        clusterer.load_existing(
            np.vstack([self._vector(0), self._vector(30), self._vector(60)]),
            np.array([7, 7, 7]),
        )

        cluster_ids, _ = clusterer.add_and_assign(
            np.vstack([self._vector(90)]),
            distance_threshold=0.15,
        )

        self.assertNotEqual(int(cluster_ids[0]), 7)

    def test_final_audit_splits_heterogeneous_rebuilt_cluster(self):
        embeddings = np.vstack(
            [self._vector(angle) for angle in [0, 10, 20, 70, 80, 90]]
        )

        result = split_heterogeneous_clusters(
            embeddings,
            np.array([7, 7, 7, 7, 7, 7]),
            distance_threshold=0.15,
        )

        self.assertEqual(len(set(result.tolist())), 2)
        self.assertEqual(len(set(result[:3].tolist())), 1)
        self.assertEqual(len(set(result[3:].tolist())), 1)
        self.assertNotEqual(result[0], result[3])

    def test_final_audit_keeps_compact_cluster(self):
        embeddings = np.vstack(
            [self._vector(angle) for angle in [0, 5, 10, 15, 20]]
        )

        result = split_heterogeneous_clusters(
            embeddings,
            np.array([7, 7, 7, 7, 7]),
            distance_threshold=0.15,
        )

        self.assertEqual(result.tolist(), [7, 7, 7, 7, 7])


if __name__ == "__main__":
    unittest.main()
