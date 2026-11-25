import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path

from backend.services.shared_sync_merge import Assignment
from backend.services.shared_sync_resolution import (
    BranchDecision,
    ConflictResolution,
    assess_resolution,
    concurrent_resolutions,
    decode_resolution,
    encode_resolution,
    mark_resolution_applied,
    record_resolution,
    shared_library_scope,
)


LIBRARY = shared_library_scope({"version": 1, "scope": "a" * 32, "generation": "b" * 32})
PHOTO = "1" * 64
SUBJECT = "2" * 64
ANNA = BranchDecision(
    "3" * 64, Assignment("person", "11111111-1111-1111-1111-111111111111", "Anna", provenance="legacy"),
)
BEA = BranchDecision(
    "4" * 64, Assignment("person", "22222222-2222-2222-2222-222222222222", "Bea", provenance="confirmed"),
)
REMOVED = BranchDecision("5" * 64, Assignment("unassigned", provenance="confirmed"))


def resolution(selected=ANNA, parents=(ANNA, BEA), *, library_scope=LIBRARY, photo_sha256=PHOTO, subject_id=SUBJECT):
    return ConflictResolution(library_scope, photo_sha256, subject_id, parents, selected.revision)


class SharedSyncResolutionTests(unittest.TestCase):
    def test_replicas_share_a_canonical_identity_without_path_or_parent_order(self):
        left = resolution()
        right = resolution(parents=(BEA, ANNA))
        self.assertEqual(left, right)
        self.assertEqual(left.event_id, right.event_id)
        wire = encode_resolution(left)
        self.assertEqual(wire, encode_resolution(right))
        wire["parents"].reverse()
        self.assertEqual(decode_resolution(wire), left)
        self.assertEqual(left.as_branch(), BranchDecision(left.event_id, ANNA.decision))
        self.assertNotEqual(
            LIBRARY, shared_library_scope({"version": 1, "scope": "a" * 32, "generation": "c" * 32})
        )

    def test_wire_rejects_unsupported_or_modified_content(self):
        wire = encode_resolution(resolution())
        with self.assertRaises(ValueError):
            decode_resolution({**wire, "selected_revision": BEA.revision})
        with self.assertRaises(ValueError):
            decode_resolution({**wire, "source_path": "must stay local"})
        with self.assertRaises(ValueError):
            decode_resolution({**wire, "version": True})
        with self.assertRaises(ValueError):
            decode_resolution({**wire, "parents": [wire["parents"][0]] * 2})
        with self.assertRaises(ValueError):
            decode_resolution({**wire, "parents": wire["parents"] * 17})

    def test_fresh_no_decision_cannot_erase_an_explicit_branch(self):
        fresh = BranchDecision("6" * 64, None)
        event = resolution(REMOVED, (ANNA, fresh, REMOVED))
        self.assertEqual(decode_resolution(encode_resolution(event)).chosen_decision.kind, "unassigned")
        with self.assertRaises(ValueError):
            resolution(fresh, (ANNA, fresh))

    def test_durable_idempotency_and_receipt_rollback_is_atomic_with_live_change(self):
        event = resolution()
        with tempfile.TemporaryDirectory() as directory:
            pathname = Path(directory) / "library.sqlite"
            with closing(sqlite3.connect(pathname)) as db:
                db.execute("BEGIN IMMEDIATE")
                self.assertTrue(record_resolution(db, event))
                self.assertFalse(record_resolution(db, event))
                db.execute("CREATE TABLE synthetic_face (name TEXT)")
                db.execute("INSERT INTO synthetic_face VALUES ('Bea')")
                db.commit()

            with closing(sqlite3.connect(pathname)) as db:
                db.execute("BEGIN IMMEDIATE")
                self.assertEqual(assess_resolution(db, event, (BEA, ANNA)), "ready")
                self.assertTrue(mark_resolution_applied(db, event, (BEA, ANNA)))
                db.execute("UPDATE synthetic_face SET name = ?", (event.chosen_decision.person_name,))
                db.rollback()
                self.assertEqual(db.execute("SELECT name FROM synthetic_face").fetchone()[0], "Bea")
                db.execute("BEGIN IMMEDIATE")
                self.assertEqual(assess_resolution(db, event, event.parents), "ready")
                self.assertTrue(mark_resolution_applied(db, event, event.parents))
                db.execute("UPDATE synthetic_face SET name = ?", (event.chosen_decision.person_name,))
                db.commit()

            with closing(sqlite3.connect(pathname)) as db:
                db.execute("BEGIN IMMEDIATE")
                self.assertEqual(assess_resolution(db, event, (event.as_branch(),)), "applied")
                self.assertFalse(mark_resolution_applied(db, event, event.parents))
                self.assertEqual(db.execute("SELECT name FROM synthetic_face").fetchone()[0], "Anna")

    def test_different_current_head_or_branch_content_remains_stale(self):
        event = resolution()
        forged = replace(ANNA, decision=REMOVED.decision)
        with closing(sqlite3.connect(":memory:")) as db:
            db.execute("BEGIN IMMEDIATE")
            record_resolution(db, event)
            self.assertEqual(assess_resolution(db, event, ()), "stale")
            self.assertEqual(assess_resolution(db, event, (ANNA, BEA, REMOVED)), "stale")
            self.assertEqual(assess_resolution(db, event, (REMOVED, BEA)), "stale")
            self.assertFalse(mark_resolution_applied(db, event, (BEA, forged)))
            record_resolution(db, resolution(BEA, (BEA, forged)))
            self.assertEqual(assess_resolution(db, event, (ANNA, BEA)), "ready")

    def test_concurrent_choices_need_a_new_resolution_of_both_heads(self):
        anna_resolution = resolution()
        bea_resolution = resolution(BEA)
        final = resolution(anna_resolution.as_branch(), (anna_resolution.as_branch(), bea_resolution.as_branch()))
        with closing(sqlite3.connect(":memory:")) as db:
            db.execute("BEGIN IMMEDIATE")
            # The later resolution may arrive before the events it references.
            record_resolution(db, final)
            self.assertEqual(assess_resolution(db, final, anna_resolution.parents), "stale")
            record_resolution(db, anna_resolution)
            self.assertTrue(mark_resolution_applied(db, anna_resolution, anna_resolution.parents))
            record_resolution(db, bea_resolution)
            self.assertEqual(assess_resolution(db, anna_resolution, (anna_resolution.as_branch(),)), "concurrent")
            self.assertEqual(assess_resolution(db, bea_resolution, bea_resolution.parents), "concurrent")
            self.assertEqual(concurrent_resolutions(db, anna_resolution), (bea_resolution,))
            self.assertFalse(mark_resolution_applied(db, bea_resolution, bea_resolution.parents))
            self.assertTrue(mark_resolution_applied(db, final, final.parents))
            self.assertEqual(assess_resolution(db, anna_resolution, (final.as_branch(),)), "applied")
            self.assertEqual(assess_resolution(db, bea_resolution, (final.as_branch(),)), "stale")
            self.assertEqual(concurrent_resolutions(db, anna_resolution), ())

    def test_scope_photo_and_subject_isolate_identical_branch_revisions(self):
        event = resolution()
        with closing(sqlite3.connect(":memory:")) as db:
            db.execute("BEGIN IMMEDIATE")
            record_resolution(db, event)
            record_resolution(db, resolution(BEA, library_scope="9" * 64))
            record_resolution(db, resolution(BEA, photo_sha256="9" * 64))
            record_resolution(db, resolution(BEA, subject_id="9" * 64))
            self.assertEqual(assess_resolution(db, event, event.parents), "ready")


if __name__ == "__main__":
    unittest.main()
