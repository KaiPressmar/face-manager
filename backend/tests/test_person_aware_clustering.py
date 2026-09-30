import unittest

import numpy as np

from backend.models.clustering import FaceClustering


class PersonAwareClusteringTest(unittest.TestCase):
    @staticmethod
    def _vector(angle_degrees: float) -> np.ndarray:
        angle = np.deg2rad(angle_degrees)
        return np.array([np.cos(angle), np.sin(angle)], dtype=np.float32)

    def _clusterer(self) -> FaceClustering:
        clusterer = FaceClustering(dim=2)
        clusterer.load_existing(
            np.vstack(
                [
                    self._vector(0),
                    self._vector(5),
                    self._vector(10),
                    self._vector(30),
                    self._vector(35),
                    self._vector(40),
                    self._vector(90),
                    self._vector(95),
                    self._vector(100),
                    self._vector(180),
                ]
            ),
            np.array([10, 10, 10, 11, 11, 11, 20, 20, 20, 30]),
            np.array([1, 1, 1, 1, 1, 1, 2, 2, 2, -1]),
        )
        return clusterer

    def test_consensus_assigns_to_closest_subcluster_of_person(self):
        cluster_ids, _ = self._clusterer().add_and_assign(
            np.vstack([self._vector(33)]),
            distance_threshold=0.3,
        )

        self.assertEqual(cluster_ids.tolist(), [11])

    def test_ambiguous_person_match_starts_unassigned_cluster(self):
        clusterer = self._clusterer()
        cluster_ids, _ = clusterer.add_and_assign(
            np.vstack([self._vector(65)]),
            distance_threshold=0.5,
        )

        self.assertGreater(cluster_ids[0], 30)

    def test_uncertain_face_can_still_join_unassigned_cluster(self):
        cluster_ids, _ = self._clusterer().add_and_assign(
            np.vstack([self._vector(178)]),
            distance_threshold=0.1,
        )

        self.assertEqual(cluster_ids.tolist(), [30])

    def test_import_mode_never_silently_assigns_a_person(self):
        clusterer = self._clusterer()
        cluster_ids, _ = clusterer.add_and_assign(
            np.vstack([self._vector(3)]),
            distance_threshold=0.3,
            allow_person_matches=False,
        )

        self.assertGreater(cluster_ids[0], 30)
        self.assertIsNone(clusterer._internal_to_person[max(clusterer._internal_to_person)])

    def test_single_near_person_face_is_not_enough_for_assignment(self):
        clusterer = FaceClustering(dim=2)
        clusterer.load_existing(
            np.vstack([self._vector(0), self._vector(90)]),
            np.array([10, 20]),
            np.array([1, 2]),
        )

        cluster_ids, _ = clusterer.add_and_assign(
            np.vstack([self._vector(2)]),
            distance_threshold=0.3,
        )

        self.assertGreater(cluster_ids[0], 20)


if __name__ == "__main__":
    unittest.main()
