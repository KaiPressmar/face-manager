import unittest
from unittest.mock import patch

from fastapi import HTTPException

from backend import app
from backend.services.cache import app_cache


class ClusterApiTest(unittest.TestCase):
    def setUp(self):
        app_cache.clear()

    @patch("backend.app.list_cluster_summaries")
    def test_clusters_returns_compact_summaries(self, list_cluster_summaries):
        list_cluster_summaries.return_value = [
            {"cluster_id": 7, "person_name": "Anna", "face_count": 12}
        ]

        result = app.api_clusters()

        list_cluster_summaries.assert_called_once_with()
        self.assertEqual(result[0]["cluster_id"], 7)
        self.assertEqual(result[0]["face_count"], 12)

    @patch("backend.app.get_cluster_summary")
    def test_cluster_detail_returns_not_found_for_missing_cluster(self, get_cluster_summary):
        get_cluster_summary.return_value = None

        with self.assertRaises(HTTPException) as raised:
            app.api_cluster_detail(12)

        self.assertEqual(raised.exception.status_code, 404)

    @patch("backend.app.get_cluster_faces")
    @patch("backend.app.get_cluster_summary")
    def test_cluster_faces_returns_cluster_payload(
        self,
        get_cluster_summary,
        get_cluster_faces,
    ):
        get_cluster_summary.return_value = {
            "cluster_id": 5,
            "person_name": "Kai",
            "face_count": 2,
        }
        get_cluster_faces.return_value = [
            {
                "id": 1,
                "image_id": 11,
                "image_path": "/photos/a.jpg",
                "bbox_x": 1,
                "bbox_y": 2,
                "bbox_w": 3,
                "bbox_h": 4,
                "cluster_id": 5,
            }
        ]

        result = app.api_cluster_faces(5)

        get_cluster_summary.assert_called_once_with(5)
        get_cluster_faces.assert_called_once_with(5)
        self.assertEqual(result["cluster_id"], 5)
        self.assertEqual(len(result["faces"]), 1)

    @patch("backend.app.assign_cluster_to_person", side_effect=LookupError("Cluster 7 not found"))
    def test_assign_person_returns_not_found_for_missing_cluster(
        self,
        assign_cluster_to_person,
    ):
        with self.assertRaises(HTTPException) as raised:
            app.api_assign_person_to_cluster(7, {"person_name": "Kai"})

        assign_cluster_to_person.assert_called_once_with(7, "Kai")
        self.assertEqual(raised.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
