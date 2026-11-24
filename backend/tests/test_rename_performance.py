import os
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import MagicMock, patch

from backend.db import schema
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
    @patch("backend.services.storage.os.path.lexists", return_value=False)
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
    @patch("backend.services.storage.os.path.lexists", return_value=False)
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


class RenameCandidateQueryTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        database = patch.object(schema, "DB_PATH", str(self.root / "database.sqlite"))
        database.start()
        self.addCleanup(database.stop)
        storage.app_cache.clear()
        self.addCleanup(storage.app_cache.clear)
        schema.init_db()

    def _cluster(self, conn, name):
        person = conn.execute("INSERT INTO person(name) VALUES (?)", (name,)).lastrowid
        return conn.execute("INSERT INTO cluster(person_id) VALUES (?)", (person,)).lastrowid

    def _location(self, conn, image_id, relative, date):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        conn.execute(
            "INSERT INTO image_location(image_id,path,directory,filename,created_at) VALUES (?,?,?,?,?)",
            (image_id, str(path), str(path.parent), path.name, date),
        )
        return str(path)

    def _photo(self, conn, relative, clusters, date="2026-01-01T00:00:00"):
        path = self.root / relative
        image_id = conn.execute(
            "INSERT INTO image(path,directory,filename) VALUES (?,?,?)",
            (str(path), str(path.parent), path.name),
        ).lastrowid
        conn.executemany(
            "INSERT INTO face(image_id,cluster_id) VALUES (?,?)",
            [(image_id, cluster) for cluster in clusters],
        )
        return image_id, self._location(conn, image_id, relative, date)

    def test_folder_filter_scopes_preview_count_selection_and_apply_to_exact_paths(self):
        with closing(schema.get_conn()) as conn:
            cluster = self._cluster(conn, "Kai")
            image_id, selected = self._photo(conn, "Family/photo.jpg", [cluster])
            inside = self._location(conn, image_id, "Family/child/copy.jpg", None)
            outside = self._location(conn, image_id, "Unselected/copy.jpg", None)
            sibling = "family" if os.sep == "/" else "FamilyArchive"
            _, other = self._photo(conn, f"{sibling}/child/other.jpg", [cluster])
            conn.commit()

        filters = {"folders": [str(self.root / "Family")]}
        page, _ = storage.list_filename_rename_candidates(**filters)
        self.assertEqual({item["path"] for item in page}, {selected, inside})
        self.assertEqual(storage.count_filename_rename_candidates(**filters), 2)
        checked = storage.list_filename_rename_candidates_for_paths(
            [outside, other, inside], **filters,
        )
        self.assertEqual([item["path"] for item in checked], [inside])

        result = storage.rename_image_locations_to_match_people(rename_all=True, **filters)
        self.assertEqual({item["from_path"] for item in result["renamed"]}, {selected, inside})
        self.assertTrue(Path(outside).is_file())
        self.assertTrue(Path(other).is_file())

    def test_older_named_photos_appear_when_first_raw_batch_has_no_named_faces(self):
        with closing(schema.get_conn()) as conn:
            cluster = self._cluster(conn, "Kai")
            _, named = self._photo(conn, "old.jpg", [cluster], "2024-01-01T00:00:00")
            for index in range(storage.MAX_IMAGE_PAGE_SIZE):
                self._photo(conn, f"new-{index}.jpg", [None], "2025-01-01T00:00:00")
            conn.commit()

        page, _ = storage.list_filename_rename_candidates(limit=2)
        self.assertEqual([item["path"] for item in page], [named])
        self.assertEqual(storage.count_filename_rename_candidates(), 1)

    def test_proposed_filename_with_separator_cannot_move_a_photo(self):
        with closing(schema.get_conn()) as conn:
            person = self._cluster(conn, "Kai/Regina")
            _, source = self._photo(conn, "Family/photo.jpg", [person])
            conn.commit()

        candidates, _ = storage.list_filename_rename_candidates()
        proposed_path = Path(candidates[0]["proposed_path"])
        proposed_path.parent.mkdir(parents=True)

        for selection in ({"selected_paths": [source]}, {"rename_all": True}):
            with self.subTest(selection=selection):
                result = storage.rename_image_locations_to_match_people(**selection)
                self.assertEqual(result["renamed_count"], 0)
                self.assertEqual(result["skipped"], [{"path": source, "reason": "invalid_filename"}])
                self.assertTrue(Path(source).is_file())
                self.assertFalse(proposed_path.exists())

    def test_person_filters_casefold_names_and_preserve_multi_person_and_unknown(self):
        with closing(schema.get_conn()) as conn:
            anne = self._cluster(conn, "Änne")
            street = self._cluster(conn, "Straße")
            both_id, both_path = self._photo(conn, "both.jpg", [anne, street])
            self._photo(conn, "one.jpg", [anne])
            unknown_id, _ = self._photo(conn, "unknown.jpg", [anne, None])
            conn.commit()

        filters = {"persons": ["änNe", "STRASSE", "Änne"]}
        rows, total = storage.list_images_page(**filters)
        self.assertEqual({row["image_id"] for row in rows}, {both_id})
        self.assertEqual(total, 1)
        page, _ = storage.list_filename_rename_candidates(**filters)
        self.assertEqual([item["path"] for item in page], [both_path])
        rows, total = storage.list_images_page(persons=["ÄNNE", "unbekannt"])
        self.assertEqual({row["image_id"] for row in rows}, {unknown_id})
        self.assertEqual(total, 1)


if __name__ == "__main__":
    unittest.main()
