import errno
import os
import stat
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from backend.db import schema
from backend.services import storage


RENAME_FILE_INFO = SimpleNamespace(
    st_mode=stat.S_IFREG, st_dev=1, st_ino=2, st_size=3, st_mtime_ns=4, st_ctime_ns=5,
)


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
    @patch("backend.services.storage._move_rename_location")
    @patch("backend.services.storage.os.path.lexists", return_value=False)
    @patch("backend.services.storage.os.lstat", return_value=RENAME_FILE_INFO)
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
            storage.filesystem_path(candidate["path"]),
            storage.filesystem_path(candidate["proposed_path"]),
            RENAME_FILE_INFO,
        )
        refresh_canonical_location.assert_called_once()
        self.assertEqual(result["renamed_count"], 1)

    @patch("backend.services.storage._refresh_canonical_image_location")
    @patch("backend.services.storage.get_conn")
    @patch("backend.services.storage._move_rename_location")
    @patch("backend.services.storage.os.path.lexists", return_value=False)
    @patch("backend.services.storage.os.lstat", return_value=RENAME_FILE_INFO)
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
            storage.filesystem_path(candidate["path"]),
            storage.filesystem_path(candidate["proposed_path"]),
            RENAME_FILE_INFO,
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

    def test_overlong_preview_cannot_be_applied_by_bulk_rename(self):
        with closing(schema.get_conn()) as conn:
            person = self._cluster(conn, "Regina")
            _, source = self._photo(conn, "photo.jpg", [person])
            conn.commit()

        with patch.object(storage, "_rename_filename_limit", return_value=12):
            candidates, _ = storage.list_filename_rename_candidates()
            self.assertEqual(candidates[0]["validation_error"], "filename_too_long")
            result = storage.rename_image_locations_to_match_people(rename_all=True)

        self.assertEqual(result["skipped"], [{"path": source, "reason": "filename_too_long"}])
        self.assertTrue(Path(source).is_file())

        with patch.object(storage, "_rename_noreplace", side_effect=OSError(errno.ENAMETOOLONG, "name too long")):
            result = storage.rename_image_locations_to_match_people(selected_paths=[source])

        self.assertEqual(result["skipped"], [{"path": source, "reason": "filename_too_long"}])
        self.assertTrue(Path(source).is_file())

    def test_preview_blocks_existing_and_shared_batch_targets(self):
        with closing(schema.get_conn()) as conn:
            anna = self._cluster(conn, "Anna")
            kai = self._cluster(conn, "Kai")
            _, one = self._photo(conn, "photo.jpg", [anna, kai])
            _, two = self._photo(conn, "photo Kai.jpg", [anna, kai])
            _, occupied_source = self._photo(conn, "occupied.jpg", [anna])
            _, clear_source = self._photo(conn, "clear.jpg", [anna])
            conn.commit()
        occupied = self.root / "occupied Anna.jpg"
        occupied.write_bytes(b"existing photo")
        selection = {"selected_paths": [one, two, occupied_source, clear_source]}
        preview = storage.preview_image_location_renames(**selection)
        rows = {item["path"]: item for item in preview["items"]}
        shared_target = str(self.root / "photo Anna, Kai.jpg")
        self.assertEqual((preview["ready_count"], preview["skipped_count"]), (1, 3))
        for path in (one, two):
            self.assertEqual(rows[path], {
                "path": path, "target_path": shared_target, "reason": "batch_target_conflict", "source_token": None,
            })
        self.assertEqual(rows[occupied_source]["reason"], "target_exists")
        self.assertIsNone(rows[clear_source]["reason"])
        self.assertFalse((self.root / "clear Anna.jpg").exists())

        occupied.unlink()
        clear_target = rows[clear_source]["target_path"]
        result = storage.rename_image_locations_to_match_people(
            **selection, confirmed_targets={clear_source: clear_target},
            confirmed_source_tokens={clear_source: rows[clear_source]["source_token"]},
        )
        self.assertEqual(result["renamed_count"], 1)
        self.assertTrue(Path(occupied_source).is_file())
        self.assertFalse(occupied.exists())

    def test_new_file_during_exclusive_move_is_never_overwritten(self):
        with closing(schema.get_conn()) as conn:
            anna = self._cluster(conn, "Anna")
            image_id, source = self._photo(conn, "photo.jpg", [anna])
            conn.commit()
        Path(source).write_bytes(b"source photo")
        preview = storage.preview_image_location_renames(selected_paths=[source])
        item = preview["items"][0]
        target = item["target_path"]
        exclusive_rename = storage._rename_noreplace

        def occupy_then_move(source_path, target_path):
            if Path(target_path) == Path(storage.filesystem_path(target)):
                Path(target_path).write_bytes(b"late arrival")
            exclusive_rename(source_path, target_path)

        with patch.object(storage, "_rename_noreplace", side_effect=occupy_then_move):
            result = storage.rename_image_locations_to_match_people(
                selected_paths=[source], confirmed_targets={source: target},
                confirmed_source_tokens={source: item["source_token"]},
            )

        self.assertEqual(result["skipped"], [{"path": source, "reason": "target_exists"}])
        self.assertEqual(Path(source).read_bytes(), b"source photo")
        self.assertEqual(Path(target).read_bytes(), b"late arrival")
        with closing(schema.get_conn()) as conn:
            self.assertEqual(conn.execute("SELECT path FROM image_location WHERE image_id = ?", (image_id,)).fetchone()["path"], source)

    def test_target_changes_after_preview_require_another_confirmation(self):
        with closing(schema.get_conn()) as conn:
            anna = self._cluster(conn, "Anna")
            _, source = self._photo(conn, "photo.jpg", [anna])
            conn.commit()
        preview = storage.preview_image_location_renames(selected_paths=[source])
        target = preview["items"][0]["target_path"]
        storage.set_filename_person_block_separator("-")
        result = storage.rename_image_locations_to_match_people(selected_paths=[source], confirmed_targets={source: target})
        self.assertEqual(result["skipped"], [{"path": source, "reason": "preview_changed"}])
        self.assertTrue(Path(source).is_file())
        self.assertFalse((self.root / "photo-Anna.jpg").exists())

    def test_replaced_source_requires_a_fresh_confirmation(self):
        with closing(schema.get_conn()) as conn:
            anna = self._cluster(conn, "Anna")
            _, source = self._photo(conn, "photo.jpg", [anna])
            conn.commit()
        source_path = Path(source)
        source_path.write_bytes(b"first photo")
        item = storage.preview_image_location_renames(selected_paths=[source])["items"][0]
        old_source = source_path.with_name("external-move.jpg")
        source_path.rename(old_source)
        source_path.write_bytes(b"replacement photo")
        result = storage.rename_image_locations_to_match_people(
            selected_paths=[source], confirmed_targets={source: item["target_path"]},
            confirmed_source_tokens={source: item["source_token"]},
        )
        self.assertEqual(result["skipped"], [{"path": source, "reason": "source_changed"}])
        self.assertEqual(source_path.read_bytes(), b"replacement photo")
        self.assertEqual(old_source.read_bytes(), b"first photo")
        self.assertFalse(Path(item["target_path"]).exists())

    def test_replacement_at_first_move_preserves_all_bytes_and_database(self):
        with closing(schema.get_conn()) as conn:
            anna = self._cluster(conn, "Anna")
            conn.commit()

        exclusive_rename = storage._rename_noreplace
        for competing_source in (False, True):
            with self.subTest(competing_source=competing_source):
                with closing(schema.get_conn()) as conn:
                    image_id, source = self._photo(conn, f"photo-{competing_source}.jpg", [anna])
                    conn.commit()
                source_path = Path(source)
                source_path.write_bytes(b"approved A")
                item = storage.preview_image_location_renames(selected_paths=[source])["items"][0]
                target = Path(item["target_path"])
                elsewhere = source_path.with_suffix(".replaced")
                retained = []

                def replace_at_move(from_path, to_path):
                    if Path(from_path) != Path(storage.filesystem_path(source)):
                        exclusive_rename(from_path, to_path)
                        return
                    source_path.rename(elsewhere)
                    source_path.write_bytes(b"unapproved B")
                    target.write_bytes(b"unrelated target")
                    exclusive_rename(from_path, to_path)
                    retained.append(Path(to_path))
                    if competing_source:
                        source_path.write_bytes(b"competing C")

                with patch.object(storage, "_rename_noreplace", side_effect=replace_at_move):
                    result = storage.rename_image_locations_to_match_people(
                        selected_paths=[source], confirmed_targets={source: str(target)},
                        confirmed_source_tokens={source: item["source_token"]},
                    )
                self.assertEqual(result["renamed_count"], 0)
                self.assertEqual(elsewhere.read_bytes(), b"approved A")
                self.assertEqual(target.read_bytes(), b"unrelated target")
                if competing_source:
                    self.assertEqual(source_path.read_bytes(), b"competing C")
                    self.assertEqual(retained[0].read_bytes(), b"unapproved B")
                    self.assertEqual(result["errors"][0]["recovery_path"], str(retained[0]))
                    self.assertIn(str(retained[0]), result["errors"][0]["reason"])
                else:
                    self.assertEqual(source_path.read_bytes(), b"unapproved B")
                    self.assertFalse(retained[0].parent.exists())
                    self.assertEqual(result["skipped"], [{"path": source, "reason": "source_changed"}])
                with closing(schema.get_conn()) as conn:
                    row = conn.execute("SELECT path FROM image_location WHERE image_id = ?", (image_id,)).fetchone()
                    self.assertEqual(row["path"], source)

    @unittest.skipIf(os.name == "nt", "Windows has a native exclusive rename")
    def test_unsupported_exclusive_rename_has_no_link_unlink_fallback(self):
        with closing(schema.get_conn()) as conn:
            anna = self._cluster(conn, "Anna")
            _, source = self._photo(conn, "photo.jpg", [anna])
            conn.commit()
        with patch.object(storage, "_exclusive_rename_function", return_value=None):
            result = storage.rename_image_locations_to_match_people(selected_paths=[source])
        self.assertEqual(result["skipped"], [{"path": source, "reason": "exclusive_rename_unavailable"}])
        self.assertTrue(Path(source).is_file())
        self.assertFalse((self.root / "photo Anna.jpg").exists())

    def test_preview_flags_two_routes_to_the_same_directory_entry(self):
        with closing(schema.get_conn()) as conn:
            anna = self._cluster(conn, "Anna")
            image_id, source = self._photo(conn, "real/photo.jpg", [anna])
            try:
                (self.root / "alias").symlink_to(self.root / "real", target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("Directory aliases are unavailable")
            alias = self._location(conn, image_id, "alias/photo.jpg", "2026-01-01T00:00:00")
            conn.commit()
        preview = storage.preview_image_location_renames(selected_paths=[source, alias])
        self.assertEqual(preview["ready_count"], 0)
        self.assertEqual([item["reason"] for item in preview["items"]], ["source_alias", "source_alias"])
        preview = storage.preview_image_location_renames(selected_paths=[source])
        self.assertEqual(preview["items"][0]["reason"], "source_alias")

    def test_case_only_move_and_distinct_hard_link_collision(self):
        with closing(schema.get_conn()) as conn:
            anna = self._cluster(conn, "Anna")
            _, source = self._photo(conn, "photo anna.jpg", [anna])
            conn.commit()
        target = self.root / "photo Anna.jpg"
        if not target.exists():
            try:
                os.link(source, target)
            except (OSError, NotImplementedError):
                self.skipTest("Hard links are unavailable")
            preview = storage.preview_image_location_renames(selected_paths=[source])
            self.assertEqual(preview["items"][0]["reason"], "target_exists")
            target.unlink()
        preview = storage.preview_image_location_renames(selected_paths=[source])
        self.assertEqual(preview["ready_count"], 1)
        result = storage.rename_image_locations_to_match_people(selected_paths=[source], confirmed_targets={source: str(target)})
        self.assertEqual(result["renamed_count"], 1)
        self.assertIn(target.name, os.listdir(self.root))
        self.assertNotIn("photo anna.jpg", os.listdir(self.root))

    def test_windows_paths_use_extended_length_and_utf16_units(self):
        with patch.object(storage.os, "name", "nt"):
            self.assertIsNone(storage._rename_filename_error("😀" * 125 + ".jpg", 255))
            self.assertEqual(storage._rename_filename_error("😀" * 126 + ".jpg", 255), "filename_too_long")
            self.assertEqual(storage._rename_filename_error("COM1.jpg", 255), "invalid_filename")
            with (
                patch.object(storage.os, "lstat", return_value=RENAME_FILE_INFO) as lstat_file,
                patch.object(storage.os.path, "lexists", return_value=False),
            ):
                source = "C:/" + ("folder/" * 45) + "photo.jpg"
                _, reason, _ = storage._rename_destination({"path": source, "proposed_filename": "photo Anna.jpg"})
                self.assertIsNone(reason)
                self.assertTrue(lstat_file.call_args.args[0].startswith("\\\\?\\C:\\"))
                too_long = "C:/" + ("folder/" * 5000) + "photo.jpg"
                _, reason, _ = storage._rename_destination({"path": too_long, "proposed_filename": "photo Anna.jpg"})
                self.assertEqual(reason, "filename_too_long")

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
