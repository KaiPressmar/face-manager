import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from backend.db import schema
from backend.services import storage


class PersonSuggestionTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        database = patch.object(schema, "DB_PATH", str(Path(directory.name) / "database.sqlite"))
        database.start()
        self.addCleanup(database.stop)
        schema.init_db()
        self.image_number = 0

    def _group(self, name=None):
        with closing(schema.get_conn()) as conn, conn:
            person_id = None
            if name is not None:
                person_id = conn.execute("INSERT INTO person(name) VALUES (?)", (name,)).lastrowid
            cluster_id = conn.execute("INSERT INTO cluster(person_id) VALUES (?)", (person_id,)).lastrowid
        return person_id, cluster_id

    def _faces(self, cluster_id, points, *, single_photo=False):
        ids = []
        image_id = None
        with closing(schema.get_conn()) as conn, conn:
            for point in points:
                if image_id is None or not single_photo:
                    self.image_number += 1
                    filename = f"{self.image_number}.jpg"
                    image_id = conn.execute(
                        "INSERT INTO image(path,directory,filename) VALUES (?, '/pictures', ?)",
                        (f"/pictures/{filename}", filename),
                    ).lastrowid
                embedding = None
                if point is not None:
                    vector = np.zeros(512, dtype=np.float32)
                    vector[:2] = point
                    embedding = (vector / np.linalg.norm(vector)).tobytes()
                ids.append(conn.execute(
                    "INSERT INTO face(image_id,cluster_id,embedding) VALUES (?,?,?)",
                    (image_id, cluster_id, embedding),
                ).lastrowid)
        return ids

    def _refresh(self):
        self.assertTrue(storage.refresh_pending_person_suggestions())
        return storage.list_person_suggestions()

    def _membership(self):
        with closing(schema.get_conn()) as conn:
            return {
                row["id"]: (row["cluster_id"], row["person_id"])
                for row in conn.execute(
                    "SELECT f.id, f.cluster_id, c.person_id FROM face f LEFT JOIN cluster c ON c.id=f.cluster_id"
                )
            }

    def _state(self):
        with closing(schema.get_conn()) as conn:
            return tuple(conn.execute("SELECT generation, processed_generation FROM person_suggestion_state").fetchone())

    @staticmethod
    def _decision(proposal, face_ids=None, scope="preview"):
        return {
            "cluster_id": proposal["cluster_id"],
            "person_id": proposal["person_id"],
            "proposal_token": proposal["proposal_token"],
            "scope": scope,
            "face_ids": proposal["preview_face_ids"] if face_ids is None else face_ids,
        }

    def test_mixed_group_scores_exact_anchors_without_moving_any_faces(self):
        anna, confirmed = self._group("Anna")
        _, second = self._group("Bob")
        _, open_group = self._group()
        references = self._faces(confirmed, [(1, 0), (.99, .01), (.98, -.01)])
        self._faces(second, [(0, 1)] * 3)
        supported = self._faces(open_group, [(1, 0)] * 6)
        residual = self._faces(open_group, [(0, 1)] * 3 + [(-1, 0), None])
        initial = self._membership()

        [proposal] = self._refresh()
        self.assertEqual(self._membership(), initial)
        self.assertEqual(proposal["cluster_id"], open_group)
        self.assertEqual(proposal["person_id"], anna)
        self.assertEqual(proposal["face_count"], 11)
        self.assertEqual(proposal["support_count"], 6)
        self.assertEqual(proposal["face_ids"], supported)
        self.assertEqual(proposal["preview_face_ids"], supported)
        self.assertEqual(len(proposal["reference_face_ids"]), 2)
        self.assertTrue(set(proposal["reference_face_ids"]).issubset(references))
        self.assertFalse(storage.refresh_pending_person_suggestions())

        self.assertEqual(storage.accept_person_suggestion_assignments([
            self._decision(proposal, supported[:2]),
        ]), 1)
        final = self._membership()
        self.assertTrue(all(final[face_id][1] == anna for face_id in supported[:2]))
        self.assertTrue(all(final[face_id] == (open_group, None) for face_id in supported[2:] + residual))
        with closing(schema.get_conn()) as conn:
            dirty = {row["person_id"] for row in conn.execute("SELECT person_id FROM recluster_dirty_person")}
        self.assertEqual(dirty, {anna, -1})

    def test_first_confirmed_person_is_useful_after_a_new_import(self):
        anna, confirmed = self._group("Anna")
        _, open_group = self._group()
        self._faces(confirmed, [(1, 0)] * 2)
        self.assertEqual(self._refresh(), [])
        faces = self._faces(open_group, [(1, 0)] * 3)
        [proposal] = self._refresh()
        self.assertEqual(proposal["face_ids"], faces)
        self.assertEqual(proposal["person_id"], anna)
        self.assertTrue(proposal["recommended"])
        self.assertEqual(proposal["runner_up_margin"], 0)

    def test_proposal_needs_support_from_three_distinct_canonical_photos(self):
        _, confirmed = self._group("Anna")
        _, open_group = self._group()
        self._faces(confirmed, [(1, 0)] * 3, single_photo=True)
        supported = self._faces(open_group, [(1, 0)] * 5, single_photo=True)
        self.assertEqual(self._refresh(), [])
        supported += self._faces(open_group, [(1, 0)])
        self.assertEqual(self._refresh(), [])
        supported += self._faces(open_group, [(1, 0)])
        [proposal] = self._refresh()
        self.assertEqual(proposal["face_ids"], supported)
        self.assertEqual(len(proposal["reference_face_ids"]), 1)

    def test_preview_cannot_confirm_a_face_only_shown_in_detail(self):
        anna, confirmed = self._group("Anna")
        _, open_group = self._group()
        self._faces(confirmed, [(1, 0)] * 2)
        supported = self._faces(open_group, [(1, 0)] * 8)
        [proposal] = self._refresh()
        self.assertEqual(proposal["preview_face_ids"], supported[:6])
        initial = self._membership()
        with self.assertRaises(storage.StalePersonSuggestionError):
            storage.accept_person_suggestion_assignments([self._decision(proposal, supported[6:])])
        self.assertEqual(self._membership(), initial)
        self.assertEqual(storage.accept_person_suggestion_assignments([
            self._decision(proposal, supported[6:], scope="detail"),
        ]), 1)
        self.assertEqual(self._membership()[supported[-1]][1], anna)
        self.assertEqual(self._membership()[supported[0]], (open_group, None))

    def test_stale_generation_invalidates_membership_references_and_profile(self):
        _, confirmed = self._group("Anna")
        _, open_group = self._group()
        references = self._faces(confirmed, [(1, 0)] * 2)
        self._faces(open_group, [(1, 0)] * 3)
        for kind in ("profile", "reference", "name", "membership"):
            with self.subTest(kind=kind):
                [proposal] = self._refresh()
                if kind == "profile":
                    storage.set_cluster_distance_threshold(.51)
                elif kind == "reference":
                    with closing(schema.get_conn()) as conn, conn:
                        conn.execute("DELETE FROM face WHERE id = ?", (references[0],))
                elif kind == "name":
                    with closing(schema.get_conn()) as conn, conn:
                        conn.execute("UPDATE person SET name = 'Anne' WHERE name = 'Anna'")
                else:
                    self._faces(open_group, [(-1, 0)])
                before = self._membership()
                self.assertEqual(storage.list_person_suggestions(), [])
                with self.assertRaises(storage.StalePersonSuggestionError):
                    storage.accept_person_suggestion_assignments([self._decision(proposal)])
                with self.assertRaises(storage.StalePersonSuggestionError):
                    storage.dismiss_person_suggestion(open_group, proposal["proposal_token"])
                self.assertEqual(self._membership(), before)

    def test_bulk_validation_is_atomic_and_legacy_payloads_are_inert(self):
        person, confirmed = self._group("Anna")
        _, first = self._group()
        _, second = self._group()
        self._faces(confirmed, [(1, 0)] * 2)
        self._faces(first, [(1, 0)] * 3)
        self._faces(second, [(1, 0)] * 3)
        initial = self._membership()
        with closing(schema.get_conn()) as conn, conn:
            conn.execute(
                """INSERT INTO cluster_person_suggestion(cluster_id, person_id, confidence, best_distance,
                   runner_up_margin, support_count, face_count, support_ratio) VALUES (?, ?, .8, .2, .1, 3, 3, 1)""",
                (first, person),
            )
        self.assertEqual(storage.list_person_suggestions(), [])
        with self.assertRaises(storage.StalePersonSuggestionError):
            storage.accept_person_suggestions(person, [first])
        with self.assertRaises(ValueError):
            storage.accept_person_suggestion_assignments([{"person_id": person, "cluster_ids": [first]}])
        self.assertEqual(self._membership(), initial)

        choices = self._refresh()
        self.assertEqual(len(choices), 2)
        valid = self._decision(choices[0])
        invalid = {**self._decision(choices[1]), "proposal_token": "stale"}
        with self.assertRaises(storage.StalePersonSuggestionError):
            storage.accept_person_suggestion_assignments([valid, invalid])
        self.assertEqual(self._membership(), initial)
        self.assertEqual(storage.accept_person_suggestion_assignments([valid, self._decision(choices[1])]), 2)

    def test_epoch_protects_old_clients_and_in_flight_workers_even_with_same_generation(self):
        _, confirmed = self._group("Anna")
        _, open_group = self._group()
        self._faces(confirmed, [(1, 0)] * 2)
        self._faces(open_group, [(1, 0)] * 3)
        [before] = self._refresh()
        old_state = self._state()
        storage.invalidate_person_suggestion_epoch()
        [after] = storage.list_person_suggestions()
        self.assertEqual(self._state(), old_state)
        self.assertNotEqual(before["proposal_token"], after["proposal_token"])
        self.assertFalse(storage.is_person_suggestion_epoch_current(before["proposal_token"]))
        self.assertTrue(storage.is_person_suggestion_epoch_current(after["proposal_token"]))
        self.assertFalse(storage.is_person_suggestion_epoch_current(storage.get_person_suggestion_epoch()))
        with self.assertRaises(storage.StalePersonSuggestionError):
            storage.accept_person_suggestion_assignments([self._decision(before)])

        self._faces(open_group, [(1, 0)])
        collect = storage._collect_person_proposals

        def rotate_epoch(conn, can_continue):
            proposals = collect(conn, can_continue)
            storage.invalidate_person_suggestion_epoch()
            return proposals

        with patch.object(storage, "_collect_person_proposals", side_effect=rotate_epoch):
            self.assertFalse(storage.refresh_pending_person_suggestions())
        self.assertEqual(storage.list_person_suggestions(), [])
        self.assertFalse(storage.refresh_pending_person_suggestions(Mock(side_effect=[True, False])))
        self.assertEqual(storage.list_person_suggestions(), [])
        self.assertEqual(self._refresh()[0]["support_count"], 4)

    def test_worker_only_publishes_the_generation_it_scored(self):
        _, confirmed = self._group("Anna")
        _, open_group = self._group()
        self._faces(confirmed, [(1, 0)] * 2)
        self._faces(open_group, [(1, 0)] * 3)
        collect = storage._collect_person_proposals

        def append_face(conn, can_continue):
            proposals = collect(conn, can_continue)
            self._faces(open_group, [(1, 0)])
            return proposals

        with patch.object(storage, "_collect_person_proposals", side_effect=append_face):
            self.assertFalse(storage.refresh_pending_person_suggestions())
        self.assertEqual(storage.list_person_suggestions(), [])
        self.assertEqual(self._refresh()[0]["support_count"], 4)

    def test_dismissal_suppresses_same_evidence_and_rejects_an_old_token(self):
        _, confirmed = self._group("Anna")
        _, open_group = self._group()
        self._faces(confirmed, [(1, 0)] * 2)
        self._faces(open_group, [(1, 0)] * 3)
        [proposal] = self._refresh()
        storage.dismiss_person_suggestion(open_group, proposal["proposal_token"])
        self.assertEqual(storage.list_person_suggestions(), [])
        storage.set_cluster_distance_threshold(.51)
        self.assertEqual(self._refresh(), [])
        self._faces(open_group, [(1, 0)])
        [changed] = self._refresh()
        self.assertEqual(changed["support_count"], 4)
        with self.assertRaises(storage.StalePersonSuggestionError):
            storage.dismiss_person_suggestion(open_group, proposal["proposal_token"])
        self.assertEqual(storage.list_person_suggestions(), [changed])


if __name__ == "__main__":
    unittest.main()
