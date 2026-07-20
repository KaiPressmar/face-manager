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

    @patch("backend.app.schedule_full_recluster")
    def test_manual_recluster_schedules_full_rebuild(self, schedule):
        schedule.return_value = {"id": "task-1"}

        result = app.api_recluster_clusters()

        schedule.assert_called_once_with("manual_recluster")
        self.assertTrue(result["scheduled"])
        self.assertEqual(result["task"]["id"], "task-1")

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

    @patch("backend.services.storage.get_cluster_faces")
    @patch("backend.services.storage.get_cluster_summary")
    @patch("backend.services.storage.list_face_review_groups")
    @patch("backend.services.storage.list_cluster_summaries")
    def test_cluster_overview_bundles_first_cluster_faces(
        self,
        list_cluster_summaries,
        list_face_review_groups,
        get_cluster_summary,
        get_cluster_faces,
    ):
        from backend.services import storage

        list_cluster_summaries.return_value = [
            {"cluster_id": 9, "cluster_label": None, "person_name": "Anna", "face_count": 3},
            {"cluster_id": 4, "cluster_label": None, "person_name": None, "face_count": 1},
        ]
        list_face_review_groups.return_value = [
            {"group_key": "unassigned", "label": "x", "face_count": 0, "cluster_count": 0},
        ]
        get_cluster_summary.return_value = {
            "cluster_id": 9,
            "person_name": "Anna",
            "face_count": 3,
        }
        get_cluster_faces.return_value = [{"id": 1, "cluster_id": 9}]

        result = storage.get_cluster_overview()

        # First (largest) cluster's faces are bundled, not the whole list.
        get_cluster_summary.assert_called_once_with(9)
        get_cluster_faces.assert_called_once_with(9)
        self.assertEqual(len(result["clusters"]), 2)
        self.assertEqual(result["review_groups"], list_face_review_groups.return_value)
        self.assertEqual(result["first_cluster"]["cluster_id"], 9)
        self.assertEqual(result["first_cluster"]["faces"], [{"id": 1, "cluster_id": 9}])

    @patch("backend.services.storage.list_face_review_groups")
    @patch("backend.services.storage.list_cluster_summaries")
    def test_cluster_overview_without_clusters_has_no_first_cluster(
        self,
        list_cluster_summaries,
        list_face_review_groups,
    ):
        from backend.services import storage

        list_cluster_summaries.return_value = []
        list_face_review_groups.return_value = []

        result = storage.get_cluster_overview()

        self.assertEqual(result["clusters"], [])
        self.assertIsNone(result["first_cluster"])

    @patch("backend.app.assign_cluster_to_person", side_effect=LookupError("Cluster 7 not found"))
    def test_assign_person_returns_not_found_for_missing_cluster(
        self,
        assign_cluster_to_person,
    ):
        with self.assertRaises(HTTPException) as raised:
            app.api_assign_person_to_cluster(7, {"person_name": "Kai"})

        assign_cluster_to_person.assert_called_once_with(7, "Kai")
        self.assertEqual(raised.exception.status_code, 404)

    @patch("backend.app.notify_clusters_changed")
    @patch("backend.app.mark_cluster_assignments_dirty")
    @patch("backend.app.reset_import_resources")
    @patch("backend.app.assign_cluster_to_person")
    def test_assignment_requests_idle_reclustering(
        self,
        assign_cluster_to_person,
        reset_import_resources,
        mark_dirty,
        notify_clusters_changed,
    ):
        result = app.api_assign_person_to_cluster(7, {"person_name": "Kai"})

        self.assertEqual(result, {"status": "ok"})
        assign_cluster_to_person.assert_called_once_with(7, "Kai")
        reset_import_resources.assert_called_once_with()
        mark_dirty.assert_called_once_with("assign_person")
        notify_clusters_changed.assert_called_once_with("assign_person")

    @patch("backend.app.notify_clusters_changed")
    @patch("backend.app.mark_cluster_assignments_dirty")
    @patch("backend.app.reset_import_resources")
    @patch("backend.app.assign_cluster_to_person")
    @patch.object(app, "import_queue")
    def test_cluster_mutation_is_not_blocked_during_import(
        self,
        import_queue,
        assign_cluster_to_person,
        reset_import_resources,
        mark_dirty,
        notify_clusters_changed,
    ):
        """A running import must never reject an interactive assignment."""
        import_queue.snapshot.return_value = {
            "running_count": 1,
            "queued_count": 0,
        }

        result = app.api_assign_person_to_cluster(7, {"person_name": "Kai"})

        self.assertEqual(result, {"status": "ok"})
        assign_cluster_to_person.assert_called_once_with(7, "Kai")

    @patch("backend.app.notify_clusters_changed")
    @patch("backend.app.rename_cluster")
    @patch.object(app, "auto_cluster_queue")
    @patch.object(app, "import_queue")
    def test_cluster_mutation_preempts_running_reclustering(
        self,
        import_queue,
        auto_cluster_queue,
        rename_cluster,
        notify_clusters_changed,
    ):
        """The write proceeds and asks the background pass to step aside."""
        import_queue.snapshot.return_value = {
            "running_count": 0,
            "queued_count": 0,
        }
        auto_cluster_queue.snapshot.return_value = {
            "task": {"status": "running", "kind": "full_recluster"},
        }

        result = app.api_rename_cluster(7, {"label": "Neu"})

        self.assertEqual(result, {"status": "ok"})
        rename_cluster.assert_called_once_with(7, "Neu")
        auto_cluster_queue.request_cancel.assert_called_once()

    @patch("backend.app.mark_cluster_assignments_dirty")
    @patch("backend.app.schedule_version_clustering_upgrade", return_value=False)
    @patch.object(app, "event_hub")
    @patch.object(app, "import_queue")
    def test_import_completion_requests_idle_reclustering(
        self,
        import_queue,
        event_hub,
        schedule_version_upgrade,
        mark_dirty,
    ):
        import_queue.snapshot.side_effect = [
            {"running_count": 1, "queued_count": 0},
            {"running_count": 0, "queued_count": 0},
        ]
        app._import_was_busy = False

        app._publish_imports()
        app._publish_imports()

        mark_dirty.assert_called_once_with("import_completed")
        schedule_version_upgrade.assert_called_once_with()
        self.assertEqual(event_hub.publish.call_count, 3)
        event_hub.publish.assert_any_call(
            "clusters",
            {"reason": "import_completed"},
        )

    @patch.object(app, "auto_cluster_queue")
    @patch.object(app, "import_queue")
    @patch("backend.app.count_reclusterable_faces", return_value=12)
    @patch("backend.app.get_applied_clustering_version", return_value="older")
    def test_new_software_version_schedules_one_full_upgrade(
        self,
        get_version,
        count_faces,
        import_queue,
        auto_cluster_queue,
    ):
        import_queue.snapshot.return_value = {"running_count": 0, "queued_count": 0}
        auto_cluster_queue.start.return_value = {
            "id": "upgrade-1",
            "reason": f"software_version:{app.APP_VERSION}",
        }
        app._version_clustering_pending = False

        self.assertTrue(app.schedule_version_clustering_upgrade())

        auto_cluster_queue.start.assert_called_once_with(
            f"software_version:{app.APP_VERSION}",
            kind="full_recluster",
            count_callable=app.count_reclusterable_faces,
            repair_callable=app._apply_version_clustering_upgrade,
        )
        self.assertFalse(app._version_clustering_pending)

    @patch.object(app, "auto_cluster_queue")
    @patch("backend.app.get_applied_clustering_version")
    def test_same_software_version_does_not_schedule_upgrade(
        self,
        get_version,
        auto_cluster_queue,
    ):
        get_version.return_value = app.APP_VERSION
        app._version_clustering_pending = True

        self.assertFalse(app.schedule_version_clustering_upgrade())

        auto_cluster_queue.start.assert_not_called()
        self.assertFalse(app._version_clustering_pending)

    @patch("backend.app.set_applied_clustering_version")
    @patch("backend.app.recluster_all_active_faces", return_value=8)
    @patch(
        "backend.app.auto_tune_cluster_distance_threshold",
        side_effect=ValueError("not enough labels"),
    )
    def test_upgrade_marks_version_only_after_successful_recluster(
        self,
        auto_tune,
        recluster,
        set_version,
    ):
        progress = object()

        rebuilt = app._apply_version_clustering_upgrade(progress_callback=progress)

        self.assertEqual(rebuilt, 8)
        auto_tune.assert_called_once_with()
        recluster.assert_called_once_with(progress_callback=progress)
        set_version.assert_called_once_with(app.APP_VERSION)

    @patch.object(app, "auto_cluster_queue")
    def test_run_startup_repairs_schedules_background_task(
        self,
        auto_cluster_queue,
    ):
        auto_cluster_queue.start.return_value = {
            "id": "autocluster-1",
            "total_faces": 3,
        }

        task = app.run_startup_repairs()

        self.assertEqual(task["id"], "autocluster-1")
        auto_cluster_queue.start.assert_called_once_with("startup")

    @patch.object(app, "auto_cluster_queue")
    def test_run_startup_repairs_returns_none_when_no_cleanup_is_needed(
        self,
        auto_cluster_queue,
    ):
        auto_cluster_queue.start.return_value = None

        task = app.run_startup_repairs()

        self.assertIsNone(task)
        auto_cluster_queue.start.assert_called_once_with("startup")

    @patch.object(app, "auto_cluster_queue")
    def test_autocluster_tasks_returns_snapshot(self, auto_cluster_queue):
        auto_cluster_queue.snapshot.return_value = {
            "task": {"id": "autocluster-1", "status": "running"},
        }

        result = app.api_autocluster_tasks()

        auto_cluster_queue.snapshot.assert_called_once_with()
        self.assertEqual(result["task"]["id"], "autocluster-1")


if __name__ == "__main__":
    unittest.main()
