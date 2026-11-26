import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from threading import Barrier
from unittest.mock import Mock, patch

from fastapi import HTTPException

from backend import app
from backend.db import schema
from backend.services import face_action_history, storage
from backend.services.shared_libraries import SharedLibraries


class FaceActionHistoryTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        db_path = Path(temporary.name) / "test.sqlite"
        database_override = patch.object(schema, "DB_PATH", str(db_path))
        database_override.start()
        self.addCleanup(database_override.stop)
        schema.init_db(scan_sources=False)
        self.execute("""
            INSERT INTO image(id, path, directory, filename)
            VALUES (1, 'photo-a.jpg', '', 'photo-a.jpg'), (2, 'photo-b.jpg', '', 'photo-b.jpg');
            INSERT INTO person(id, name) VALUES (1, 'Anna'), (2, 'Ben');
            INSERT INTO cluster(id, label, person_id) VALUES (7, 'Ausgangsgruppe', 1), (8, 'Archiv', NULL);
            INSERT INTO face(id, image_id, cluster_id, review_status, bbox_x, bbox_y, bbox_w, bbox_h)
            VALUES (1, 1, 7, 'active', 10, 10, 10, 10), (2, 2, 7, 'active', 10, 10, 10, 10),
                   (3, 1, 8, 'unknown_person', 20, 20, 10, 10), (4, 1, NULL, 'active', 50, 50, 10, 10);
        """)

    def execute(self, sql):
        with closing(schema.get_conn()) as connection, connection:
            connection.executescript(sql)

    def faces(self):
        with closing(schema.get_conn()) as connection:
            return [tuple(row) for row in connection.execute("""
                SELECT f.id, f.cluster_id, f.review_status, p.name
                FROM face f LEFT JOIN cluster c ON c.id = f.cluster_id
                LEFT JOIN person p ON p.id = c.person_id ORDER BY f.id
            """)]

    def replay(self, *, redo=False, history=None):
        history = history or app.api_face_action_history()
        action = history["redo"] if redo else history["undo"]
        payload = app._FaceActionReplay(
            action_id=action["id"], revision=history["revision"], library_id=history["library_id"],
        )
        return (app.api_redo_face_action if redo else app.api_undo_face_action)(payload)

    def test_real_entry_points_save_single_group_and_archive_actions(self):
        original = self.faces()
        assigned = app.api_batch_update_faces({"action": "assign_person", "face_ids": [1], "person_name": "Ben"})
        self.assertEqual(assigned["history_action"]["person_name"], "Ben")
        self.assertEqual(assigned["history_action"]["face_count"], 1)
        assigned_faces = self.faces()
        self.assertEqual(assigned_faces[0][3], "Ben")
        self.assertEqual(assigned_faces[1:], original[1:])
        history = app.api_face_action_history()
        self.assertEqual(history["undo"], assigned["history_action"])
        self.assertEqual(self.replay(history=history)["action"]["undone"], True)
        self.assertEqual(self.faces(), original)
        with self.assertRaises(HTTPException) as duplicate:
            self.replay(history=history)
        self.assertEqual(duplicate.exception.status_code, 409)
        self.replay(redo=True)
        self.assertEqual(self.faces(), assigned_faces)

        group = app.api_batch_update_faces({"action": "assign_person", "face_ids": [1, 2], "person_name": "Clara"})
        self.assertEqual(group["history_action"]["face_count"], 2)
        group_faces = self.faces()
        self.replay()
        self.assertEqual(self.faces(), assigned_faces)
        self.replay(redo=True)
        self.assertEqual(self.faces(), group_faces)

        for action, status in (("mark_unknown_person", "unknown_person"), ("mark_not_face", "not_face")):
            with self.subTest(action=action):
                before = self.faces()
                archive = app.api_batch_update_faces({"action": action, "face_ids": [1, 2]})
                self.assertEqual(archive["history_action"]["face_count"], 2)
                after = self.faces()
                self.assertEqual([row[2:] for row in after[:2]], [(status, None)] * 2)
                self.replay()
                self.assertEqual(self.faces(), before)
                self.replay(redo=True)
                self.assertEqual(self.faces(), after)

    def test_stack_is_persisted_and_a_new_action_invalidates_redo(self):
        app.api_batch_update_faces({"action": "assign_person", "face_ids": [1], "person_name": "Ben"})
        app.api_batch_update_faces({"action": "mark_not_face", "face_ids": [1]})
        after_two = self.faces()
        schema.init_db(scan_sources=False)
        first_undo = self.replay()
        self.assertEqual(self.faces()[0][2:], ("active", "Ben"))
        self.replay(history=first_undo["history"])
        self.assertEqual(self.faces()[0][2:], ("active", "Anna"))
        self.replay(redo=True)
        self.replay(redo=True)
        self.assertEqual(self.faces(), after_two)

        self.replay()
        obsolete = app.api_face_action_history()
        self.assertIsNotNone(obsolete["redo"])
        app.api_batch_update_faces({"action": "mark_not_face", "face_ids": [3]})
        self.assertIsNone(app.api_face_action_history()["redo"])
        with self.assertRaises(HTTPException) as rejected:
            self.replay(redo=True, history=obsolete)
        self.assertEqual(rejected.exception.status_code, 409)

    def test_parallel_replays_consume_an_action_revision_once(self):
        app.api_batch_update_faces({"action": "mark_not_face", "face_ids": [3]})
        history = app.api_face_action_history()
        start = Barrier(2)

        def attempt():
            start.wait(timeout=3)
            try:
                return self.replay(history=history)["status"]
            except HTTPException as conflict:
                return conflict.status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [pool.submit(attempt) for _ in range(2)]
            self.assertCountEqual([result.result(timeout=5) for result in results], ["ok", 409])
        self.assertEqual(self.faces()[2][2], "unknown_person")

    def test_background_regrouping_keeps_assignments_reversible(self):
        app.api_batch_update_faces({"action": "assign_person", "face_ids": [1], "person_name": "Ben"})
        prior_cluster = self.faces()[0][1]
        storage.recluster_all_active_faces()
        self.assertNotEqual(self.faces()[0][1], prior_cluster)
        self.assertEqual(self.faces()[0][3], "Ben")
        self.replay()
        self.assertEqual(self.faces()[0][3], "Anna")
        self.assertEqual(self.faces()[1][3], "Anna")
        self.replay(redo=True)
        self.assertEqual(self.faces()[0][1:], (prior_cluster, "active", "Ben"))

    def test_stale_expected_state_and_prior_library_id_are_rejected(self):
        history = app.api_face_action_history()
        with self.assertRaises(HTTPException) as rejected:
            app.api_batch_update_faces({
                "action": "mark_not_face", "face_ids": [1],
                "expected_faces": [{"id": 1, "cluster_id": 7, "review_status": "active", "person_name": "Ben"}],
                "expected_library_id": history["library_id"],
            })
        self.assertEqual(rejected.exception.status_code, 409)
        self.assertEqual(self.faces()[0][3], "Anna")
        batch = app.api_batch_update_faces({"action": "mark_not_face", "face_ids": [1]})
        with patch("backend.app.get_person_suggestion_epoch", return_value="a-new-library"):
            with self.assertRaises(HTTPException) as moved:
                app.api_undo_face_action(app._FaceActionReplay(
                    action_id=batch["history_action"]["id"], library_id=history["library_id"], revision=history["revision"] + 1,
                ))
        self.assertEqual(moved.exception.status_code, 409)
        self.assertEqual(self.faces()[0][2], "not_face")

    def test_manual_changes_cannot_be_overwritten_even_if_a_value_is_restored(self):
        app.api_batch_update_faces({"action": "assign_person", "face_ids": [1], "person_name": "Ben"})
        before = self.faces()
        self.execute("UPDATE face SET bbox_x = 20 WHERE id = 1; UPDATE face SET bbox_x = 10 WHERE id = 1;")
        with self.assertRaises(HTTPException) as changed:
            self.replay()
        self.assertEqual(changed.exception.status_code, 409)
        self.assertEqual(self.faces(), before)

    def test_replay_never_changes_outside_members_of_a_reused_group(self):
        app.api_batch_update_faces({"action": "mark_unknown_person", "face_ids": [1, 2]})
        self.execute("UPDATE face SET cluster_id = 7 WHERE id = 4;")
        separate_member = self.faces()
        with self.assertRaises(HTTPException) as rejected:
            self.replay()
        self.assertEqual(rejected.exception.status_code, 409)
        self.assertEqual(self.faces(), separate_member)

    def test_assignment_and_replay_roll_back_if_history_write_or_restore_fails(self):
        original = self.faces()
        self.execute("""
            CREATE TRIGGER fail_action_journal BEFORE INSERT ON face_action_history
            BEGIN SELECT RAISE(ABORT, 'journal failure'); END;
        """)
        with self.assertRaises(sqlite3.IntegrityError):
            storage.assign_faces_to_person([1], "Ben", record_history=True)
        self.assertEqual(self.faces(), original)
        self.assertEqual(app.api_face_action_history()["entries"], [])
        self.execute("DROP TRIGGER fail_action_journal;")
        app.api_batch_update_faces({"action": "assign_person", "face_ids": [1], "person_name": "Ben"})
        action = app.api_face_action_history()
        changed = self.faces()

        real_restore = face_action_history._restore_states

        def fail_restore(*args):
            real_restore(*args)
            raise sqlite3.OperationalError("after writing")

        with patch.object(face_action_history, "_restore_states", side_effect=fail_restore):
            with self.assertRaises(sqlite3.OperationalError):
                self.replay(history=action)
        self.assertEqual(self.faces(), changed)
        self.assertEqual(app.api_face_action_history(), action)

    def test_writable_share_blocks_history_but_allows_regular_assignment(self):
        initial = app.api_batch_update_faces({"action": "assign_person", "face_ids": [1], "person_name": "Ben"})
        history = app.api_face_action_history()
        coordinator = SharedLibraries()
        profile = Mock()
        profile.snapshot.return_value = {"source_path": "shared", "write_enabled": True, "paused": True}
        coordinator._profiles["library"] = profile
        with (
            patch("backend.app.shared_library_preview", coordinator),
            patch.object(coordinator, "write_available", return_value=True),
        ):
            self.assertTrue(app.api_face_action_history()["blocked_reason"])
            with self.assertRaises(HTTPException) as guarded:
                self.replay(history=history)
            self.assertEqual(guarded.exception.status_code, 409)
            result = app.api_batch_update_faces({"action": "assign_person", "face_ids": [4], "person_name": "Ben"})
            self.assertNotIn("history_action", result)
        self.assertEqual(app.api_face_action_history()["undo"], initial["history_action"])
        self.assertEqual(self.faces()[3][3], "Ben")
