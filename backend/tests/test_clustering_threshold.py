import unittest

import numpy as np

from backend.models.clustering import tune_distance_threshold
from backend.services.storage import _derive_clustering_profile


class ClusteringThresholdTuningTest(unittest.TestCase):
    @staticmethod
    def _vector(angle_degrees: float) -> np.ndarray:
        angle = np.deg2rad(angle_degrees)
        result = np.zeros(512, dtype=np.float32)
        result[0] = np.cos(angle)
        result[1] = np.sin(angle)
        return result

    def test_tunes_between_same_and_different_person_distances(self):
        embeddings = np.vstack(
            [
                self._vector(0),
                self._vector(20),
                self._vector(80),
                self._vector(100),
            ]
        )

        result = tune_distance_threshold(embeddings, np.array([1, 1, 2, 2]))

        self.assertGreaterEqual(result["threshold"], 0.07)
        self.assertLess(result["threshold"], 0.5)
        self.assertEqual(result["person_count"], 2)
        self.assertEqual(result["balanced_accuracy"], 1.0)

    def test_people_are_balanced_instead_of_faces(self):
        person_one = [self._vector(angle) for angle in range(0, 20, 2)]
        embeddings = np.vstack(person_one + [self._vector(80), self._vector(90)])
        labels = np.array([1] * len(person_one) + [2, 2])

        result = tune_distance_threshold(embeddings, labels)

        self.assertEqual(result["person_count"], 2)
        self.assertGreater(result["different_person_accuracy"], 0.9)

    def test_existing_subclusters_prevent_overly_broad_person_threshold(self):
        embeddings = np.vstack(
            [self._vector(angle) for angle in [0, 5, 60, 65, 120, 125]]
        )
        people = np.array([1, 1, 1, 1, 2, 2])

        person_wide = tune_distance_threshold(embeddings, people)
        subcluster_aware = tune_distance_threshold(
            embeddings,
            people,
            cluster_ids=np.array([10, 10, 11, 11, 20, 20]),
        )

        self.assertLess(subcluster_aware["threshold"], person_wide["threshold"])
        self.assertEqual(subcluster_aware["balanced_accuracy"], 1.0)
        self.assertTrue(subcluster_aware["cohesion_aware"])

    def test_requires_two_people_and_a_same_person_pair(self):
        with self.assertRaisesRegex(ValueError, "at least two people"):
            tune_distance_threshold(
                np.vstack([self._vector(0), self._vector(10), self._vector(20)]),
                np.array([1, 1, 1]),
            )

        with self.assertRaisesRegex(ValueError, "needs two assigned faces"):
            tune_distance_threshold(
                np.vstack([self._vector(0), self._vector(80), self._vector(160)]),
                np.array([1, 2, 3]),
            )

    def test_user_strictness_maps_to_one_coherent_internal_profile(self):
        strict_profile = _derive_clustering_profile(0.30)
        permissive_profile = _derive_clustering_profile(0.70)

        self.assertLess(
            strict_profile["neighbor_threshold"],
            permissive_profile["neighbor_threshold"],
        )
        self.assertLess(
            strict_profile["cohesion_threshold"],
            permissive_profile["cohesion_threshold"],
        )
        self.assertLess(
            strict_profile["person_anchor_threshold"],
            permissive_profile["person_anchor_threshold"],
        )
        self.assertEqual(strict_profile["cluster_support_ratio"], 0.80)


if __name__ == "__main__":
    unittest.main()
