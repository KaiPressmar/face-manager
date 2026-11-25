import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path

from backend.services.shared_sync_conflicts import (
    ConflictEvidencePending,
    apply_verified_choice,
    build_conflict_case,
    reserve_conflict_guard,
    stage_conflict_choice,
    validate_conflict_packet,
    verified_current_parents,
)
from backend.services.shared_sync_journal import ExchangeJournal, digest
from backend.services.shared_sync_merge import Assignment, FaceEvidence
from backend.services.shared_sync_reconcile import photo_event
from backend.services.shared_sync_snapshot import SnapshotPhoto


PHOTO = "a" * 64
OTHER_PHOTO = "b" * 64
PATH = "Holiday/Photo.jpg"
MANIFEST = {"version": 1, "scope": "a" * 32, "generation": "b" * 32}
REMOTE = "c" * 32
OWN_FACE = "11111111-1111-1111-1111-111111111111"
PEER_FACE = "22222222-2222-2222-2222-222222222222"
ANNA = Assignment("person", "33333333-3333-3333-3333-333333333333", "Anna", provenance="legacy")
BEA = Assignment("person", "44444444-4444-4444-4444-444444444444", "Bea", provenance="confirmed")


def photo(face_id, decision, *, box=(0.1, 0.1, 0.3, 0.3), sha=PHOTO):
    face = FaceEvidence(face_id, sha, box, decision, photo_verified=True, geometry_reliable=True)
    return SnapshotPhoto(1, sha, (PATH,), (face,), 100, 100, 1)


def receipt(journal, scope, producer, batch_number, events, *, sequence=None):
    first = batch_number if sequence is None else sequence
    batch = {
        "producer_id": producer, "batch_no": batch_number, "first_sequence": first,
        "last_sequence": first + len(events) - 1, "events": events,
    }
    journal.accept(scope, producer, batch, digest(batch))


class SharedConflictEvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.journal = ExchangeJournal(Path(temporary.name) / "witness.sqlite")
        self.journal.initialize()
        self.journal.bind("local", MANIFEST)
        self.own = self.journal.checkpoint("local")["producer_id"]
        self.local_photo = photo(OWN_FACE, ANNA)
        self.remote_photo = photo(PEER_FACE, BEA)
        self.first = photo_event(self.local_photo, "Fresh device")
        self.second = photo_event(self.remote_photo, "Existing device")
        self.journal.stage("local", [self.first])

    def receive_pair(self):
        receipt(self.journal, "local", self.own, 1, [self.first])
        receipt(self.journal, "local", REMOTE, 1, [self.second])

    def case(self, local=None, *, path=PATH):
        return build_conflict_case(
            self.journal, "local", MANIFEST, self.own, REMOTE, local or self.local_photo,
            OWN_FACE, PEER_FACE, verified_hash=PHOTO, verified_relative_path=path,
        )

    def validate(self, packet, *, author=REMOTE, manifest=MANIFEST):
        return validate_conflict_packet(self.journal, "local", manifest, self.own, REMOTE, author, packet)

    def test_only_acknowledged_exact_current_pair_produces_order_independent_case(self):
        receipt(self.journal, "local", REMOTE, 1, [self.second])
        self.assertIsNone(self.case())
        receipt(self.journal, "local", self.own, 1, [self.first])
        case = self.case()
        inverse = build_conflict_case(
            self.journal, "local", MANIFEST, REMOTE, self.own, self.remote_photo, PEER_FACE, OWN_FACE,
            verified_hash=PHOTO, verified_relative_path=PATH,
        )
        self.assertEqual(case.revision, inverse.revision)
        self.assertEqual(case.packet(case.peer_branch.revision), inverse.packet(inverse.local_branch.revision))
        self.assertIsNone(self.case(photo(OWN_FACE, BEA)))
        self.assertIsNone(self.case(path="Private/Photo.jpg"))

    def test_ambiguous_geometry_does_not_become_actionable(self):
        self.receive_pair()
        original = self.local_photo.faces[0]
        extra = replace(original, face_id="55555555-5555-5555-5555-555555555555")
        self.assertIsNone(self.case(replace(self.local_photo, faces=(original, extra))))

    def test_resolution_retries_when_original_evidence_has_not_arrived(self):
        self.receive_pair()
        case = self.case()
        packet = case.packet(case.peer_branch.revision)
        lagging = ExchangeJournal(self.journal.path.parent / "lagging.sqlite")
        lagging.initialize()
        lagging.bind("local", MANIFEST)
        receipt(lagging, "local", self.own, 1, [self.first])
        with self.assertRaises(ConflictEvidencePending):
            validate_conflict_packet(lagging, "local", MANIFEST, self.own, REMOTE, REMOTE, packet)
        receipt(lagging, "local", REMOTE, 1, [self.second])
        self.assertEqual(validate_conflict_packet(lagging, "local", MANIFEST, self.own, REMOTE, REMOTE, packet).case, case)

    def test_old_exact_proofs_can_be_verified_after_newer_snapshot_without_becoming_current(self):
        self.receive_pair()
        case = self.case()
        packet = case.packet(case.peer_branch.revision)
        receipt(self.journal, "local", REMOTE, 2, [photo_event(photo(PEER_FACE, ANNA), "Existing device")])
        self.assertIsNone(self.case())
        historical = self.validate(packet)
        self.assertEqual(historical.case, case)
        self.assertEqual(historical.resolution.chosen_decision, BEA)
        for modified in (
            {**packet, "relative_paths": ["Elsewhere/Photo.jpg"]},
            {**packet, "photo_sha256": OTHER_PHOTO},
            {**packet, "proofs": [{**packet["proofs"][0], "batch": 2}, packet["proofs"][1]]},
        ):
            with self.assertRaises(ValueError):
                self.validate(modified)
        with self.assertRaises(ValueError):
            self.validate(packet, author="d" * 32)
        with self.assertRaises(ValueError):
            self.validate(packet, manifest={**MANIFEST, "generation": "e" * 32})

    def test_lagging_replica_accepts_only_peer_republication_of_the_chosen_exact_face(self):
        self.receive_pair()
        case = self.case()
        verified = self.validate(case.packet(case.local_branch.revision))
        parents = (case.local_branch, case.peer_branch)
        self.assertEqual(verified_current_parents(self.journal, "local", verified, self.local_photo), parents)
        self.assertEqual(verified_current_parents(self.journal, "local", verified, photo(OWN_FACE, BEA)), ())

        chosen = replace(ANNA, provenance="confirmed")
        published = photo_event(photo(PEER_FACE, chosen), "Existing device")
        receipt(self.journal, "local", REMOTE, 2, [published])
        self.assertEqual(verified_current_parents(self.journal, "local", verified, self.local_photo), parents)
        moved = photo_event(photo(PEER_FACE, chosen, box=(0.11, 0.1, 0.3, 0.3)), "Existing device")
        receipt(self.journal, "local", REMOTE, 3, [moved])
        self.assertEqual(verified_current_parents(self.journal, "local", verified, self.local_photo), ())
        later_edit = photo_event(photo(PEER_FACE, replace(chosen, person_name="Different name")), "Existing device")
        receipt(self.journal, "local", REMOTE, 4, [later_edit])
        self.assertEqual(verified_current_parents(self.journal, "local", verified, self.local_photo), ())
        receipt(self.journal, "local", REMOTE, 5, [published])
        self.journal.stage("local", [self.first])
        receipt(self.journal, "local", self.own, 2, [self.first])
        self.assertEqual(verified_current_parents(self.journal, "local", verified, self.local_photo), ())

    def test_guard_only_receipt_preserves_regular_dirty_snapshot(self):
        self.receive_pair()
        case = self.case()
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.execute("CREATE TABLE shared_sync_outbox_scope(id TEXT PRIMARY KEY, revision INTEGER NOT NULL)")
            conn.execute(
                "CREATE TABLE shared_sync_dirty_image(scope_id TEXT, image_id INTEGER, revision INTEGER,"
                " PRIMARY KEY(scope_id,image_id))"
            )
            conn.execute("INSERT INTO shared_sync_outbox_scope VALUES ('images', 4)")
            guard = reserve_conflict_guard(conn, "images", 1)
            with self.assertRaises(ValueError):
                stage_conflict_choice(conn, self.journal, "local", "images", case, "stale",
                                      case.peer_branch.revision, guard)
            stage_conflict_choice(conn, self.journal, "local", "images", case, case.revision,
                                  case.peer_branch.revision, guard)
            outgoing = self.journal.checkpoint("local")["outgoing"]
            self.assertEqual(outgoing["events"], [case.packet(case.peer_branch.revision)])
            self.assertEqual(self.journal.pending_local_rows("local", 2), (False, [[1, 5]]))
            receipt(self.journal, "local", self.own, 2, outgoing["events"])
            self.assertEqual(self.journal.acknowledged_local_rows("local"), (2, []))
            self.assertEqual(conn.execute("SELECT revision FROM shared_sync_dirty_image").fetchone()[0], 5)

    def test_only_exact_case_applies_and_replay_or_competing_choice_does_not_mutate(self):
        self.receive_pair()
        case = self.case()
        packet = case.packet(case.peer_branch.revision)
        verified = self.validate(json.loads(json.dumps(packet)))
        competing = self.validate(case.packet(case.local_branch.revision), author=self.own)
        calls = []

        def write(decision, event_id):
            calls.append((decision, event_id))

        with closing(sqlite3.connect(":memory:")) as conn:
            conn.execute("BEGIN IMMEDIATE")
            self.assertEqual(apply_verified_choice(conn, verified, (), write), "stale")
            parents = (case.local_branch, case.peer_branch)
            self.assertEqual(apply_verified_choice(conn, verified, parents, write), "applied")
            self.assertEqual(apply_verified_choice(conn, verified, parents, write), "applied")
            self.assertEqual(apply_verified_choice(conn, competing, parents, write), "concurrent")
        self.assertEqual(calls, [(BEA, verified.resolution.event_id)])

    def test_failed_live_write_leaves_event_unapplied_even_if_caller_catches_error(self):
        self.receive_pair()
        case = self.case()
        verified = self.validate(case.packet(case.peer_branch.revision))
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("CREATE TABLE synthetic_face (name TEXT)")
            conn.execute("INSERT INTO synthetic_face VALUES ('Anna')")

            def fail(decision, _event_id):
                conn.execute("UPDATE synthetic_face SET name = ?", (decision.person_name,))
                raise ValueError("Cannot finish the exact update")

            parents = (case.local_branch, case.peer_branch)
            with self.assertRaises(ValueError):
                apply_verified_choice(conn, verified, parents, fail)
            self.assertEqual(conn.execute("SELECT name FROM synthetic_face").fetchone()[0], "Anna")
            self.assertEqual(conn.execute("SELECT applied FROM shared_sync_resolution").fetchone()[0], 0)

    def test_resolution_receipt_iterator_pages_in_acknowledgement_order(self):
        self.receive_pair()
        case = self.case()
        local = case.packet(case.local_branch.revision)
        remote = case.packet(case.peer_branch.revision)
        self.journal.stage("local", [local])
        self.assertEqual(self.journal.resolution_packets("local", 0), [])
        receipt(self.journal, "local", REMOTE, 2, [self.second, remote])
        receipt(self.journal, "local", self.own, 2, [local])
        first, = self.journal.resolution_packets("local", 0, 1)
        second, = self.journal.resolution_packets("local", first[0], 1)
        self.assertEqual((first[1].producer, first[1].position, first[1].event), (REMOTE, 1, remote))
        self.assertEqual((second[1].producer, second[1].position, second[1].event), (self.own, 0, local))
        receipt(self.journal, "local", self.own, 2, [local])
        self.assertEqual(self.journal.resolution_packets("local", second[0]), [])
        self.assertEqual(self.journal.resolution_packets("other-scope", 0), [])

    def test_legacy_journal_indexes_only_acknowledged_packets_and_migrates_once(self):
        self.receive_pair()
        case = self.case()
        remote = case.packet(case.peer_branch.revision)
        receipt(self.journal, "local", REMOTE, 2, [remote])
        self.journal.stage("local", [case.packet(case.local_branch.revision)])
        with closing(sqlite3.connect(self.journal.path)) as conn, conn:
            conn.execute("DROP TABLE shared_resolution_packet")
            conn.execute("PRAGMA user_version = 1")
        reopened = ExchangeJournal(self.journal.path)
        reopened.initialize()
        (sequence, entry), = reopened.resolution_packets("local", 0)
        self.assertEqual(entry.event, remote)
        reopened.initialize()
        self.assertEqual(reopened.resolution_packets("local", 0), [(sequence, entry)])


if __name__ == "__main__":
    unittest.main()
