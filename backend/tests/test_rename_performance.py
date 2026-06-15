import unittest
from unittest.mock import MagicMock, patch

from backend.services import storage


class RenamePerformanceTest(unittest.TestCase):
    def _candidate(self):
        return {
            "location_id": 1,
            "image_id": 2,
            "path": "/photos/source.jpg",
            "directory": "/photos",
            "created_at": "2026-06-01T00:00:00+00:00",
            "current_filename": "source.jpg",
            "proposed_filename": "source Kai.jpg",
            "proposed_path": "/photos/source Kai.jpg",
            "detected_person_names": ["Kai"],
            "current_suffix_person_names": [],
        }

    @patch("backend.services.storage._refresh_canonical_image_location")
    @patch("backend.services.storage.get_conn")
    @patch("backend.services.storage.os.rename")
    @patch("backend.services.storage.os.path.exists", return_value=False)
    @patch("backend.services.storage.os.path.isfile", return_value=True)
    @patch("backend.services.storage.list_all_filename_rename_candidates")
    @patch("backend.services.storage.list_filename_rename_candidates_for_paths")
    def test_selected_path_rename_skips_full_candidate_scan(
        self,
        list_for_paths,
        list_all_candidates,
        _,
        __,
        rename_file,
        get_conn,
        refresh_canonical_location,
    ):
        candidate = self._candidate()
        list_for_paths.return_value = [candidate]
        conn = MagicMock()
        conn.cursor.return_value = MagicMock()
        get_conn.return_value = conn

        result = storage.rename_image_locations_to_match_people(
            selected_paths=[candidate["path"]],
            folders=["/photos"],
            persons=["Kai"],
            sort_by="date",
            sort_direction="desc",
        )

        list_all_candidates.assert_not_called()
        list_for_paths.assert_called_once_with(
            [candidate["path"]],
            suffix_format=None,
            folders=["/photos"],
            persons=["Kai"],
        )
        rename_file.assert_called_once_with(
            candidate["path"],
            candidate["proposed_path"],
        )
        refresh_canonical_location.assert_called_once()
        self.assertEqual(result["renamed_count"], 1)

    @patch("backend.services.storage._refresh_canonical_image_location")
    @patch("backend.services.storage.get_conn")
    @patch("backend.services.storage.os.rename")
    @patch("backend.services.storage.os.path.exists", return_value=False)
    @patch("backend.services.storage.os.path.isfile", return_value=True)
    @patch("backend.services.storage.list_all_filename_rename_candidates")
    @patch("backend.services.storage.list_filename_rename_candidates_for_paths")
    def test_rename_all_keeps_full_candidate_scan(
        self,
        list_for_paths,
        list_all_candidates,
        _,
        __,
        rename_file,
        get_conn,
        refresh_canonical_location,
    ):
        candidate = self._candidate()
        list_all_candidates.return_value = [candidate]
        conn = MagicMock()
        conn.cursor.return_value = MagicMock()
        get_conn.return_value = conn

        result = storage.rename_image_locations_to_match_people(
            rename_all=True,
            folders=["/photos"],
            persons=["Kai"],
            sort_by="date",
            sort_direction="desc",
        )

        list_for_paths.assert_not_called()
        list_all_candidates.assert_called_once_with(
            suffix_format=None,
            folders=["/photos"],
            persons=["Kai"],
            sort_by="date",
            sort_direction="desc",
        )
        rename_file.assert_called_once_with(
            candidate["path"],
            candidate["proposed_path"],
        )
        refresh_canonical_location.assert_called_once()
        self.assertEqual(result["renamed_count"], 1)


if __name__ == "__main__":
    unittest.main()
