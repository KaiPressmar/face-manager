import unittest

import numpy as np

from backend.models.clustering import order_embeddings_by_similarity


class FaceSimilarityOrderTest(unittest.TestCase):
    @staticmethod
    def _vector(angle_degrees: float) -> np.ndarray:
        angle = np.deg2rad(angle_degrees)
        return np.array([np.cos(angle), np.sin(angle)], dtype=np.float32)

    def test_keeps_separated_appearance_groups_contiguous(self):
        embeddings = np.vstack(
            [
                self._vector(175),
                self._vector(5),
                self._vector(180),
                self._vector(0),
                self._vector(170),
                self._vector(10),
            ]
        )
        group_ids = np.array([2, 1, 2, 1, 2, 1])

        order = order_embeddings_by_similarity(
            embeddings,
            stable_ids=np.array([60, 10, 50, 20, 40, 30]),
        )
        ordered_groups = group_ids[order]

        transitions = np.count_nonzero(ordered_groups[1:] != ordered_groups[:-1])
        self.assertEqual(transitions, 1)
        self.assertEqual(sorted(order.tolist()), list(range(6)))

    def test_order_is_stable_for_identical_embeddings(self):
        embeddings = np.vstack([self._vector(0)] * 4)
        ids = np.array([40, 10, 30, 20])

        first = order_embeddings_by_similarity(embeddings, ids)
        second = order_embeddings_by_similarity(embeddings, ids)

        self.assertEqual(first.tolist(), second.tolist())

    def test_validates_stable_id_alignment(self):
        with self.assertRaisesRegex(ValueError, "Stable IDs"):
            order_embeddings_by_similarity(
                np.vstack([self._vector(0), self._vector(10)]),
                np.array([1]),
            )


if __name__ == "__main__":
    unittest.main()
