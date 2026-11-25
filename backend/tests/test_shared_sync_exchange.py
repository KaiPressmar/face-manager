import hashlib
import os
import tempfile
import time
import unittest
import uuid
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from backend.db import schema
from backend.services.shared_sync_io import SharedSyncProcess
from backend.services.shared_sync_journal import ExchangeJournal, SharedStateChanged, digest, encode
from backend.services.shared_sync_merge import Assignment, FaceEvidence
from backend.services.shared_sync_preview import SharedLibraryPreview
from backend.services.shared_sync_reconcile import compare_verified_photo, photo_event
from backend.services.shared_sync_snapshot import SnapshotPhoto
from backend.services.shared_sync_transport import ShareTransport, TransportError
from backend.services.shared_sync_worker import run_request


class SharedExchangeTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.journal = ExchangeJournal(self.root / "local.sqlite")
        self.journal.initialize()

    def transport(self):
        transport = ShareTransport(self.root, allow_writes=True)
        if not transport.probe_writes():
            self.skipTest("Local filesystem does not support no-replace publication")
        return transport

    def test_pc2_keeps_its_previous_name_after_pc1_redetects_the_same_photo(self):
        photo_path = self.root / "family.jpg"
        photo_path.write_bytes(b"The same test photo on both devices")
        photo_hash = hashlib.sha256(photo_path.read_bytes()).hexdigest()
        local_face = FaceEvidence(
            str(uuid.uuid4()), photo_hash, (0.10, 0.20, 0.40, 0.60),
            Assignment("person", str(uuid.uuid4()), "Anna", provenance="legacy"), True, True,
        )
        shared_face = FaceEvidence(
            str(uuid.uuid4()), photo_hash, (0.10, 0.20, 0.39, 0.59), None, True, True,
        )
        local = SnapshotPhoto(400, photo_hash, ("family.jpg",), (local_face,), 1280, 900, 1)
        fresh = SnapshotPhoto(3, photo_hash, ("family.jpg",), (shared_face,), 1280, 900, 1)
        event = photo_event(fresh, "PC1")
        self.assertNotIn("image_id", event)

        transport = self.transport()
        manifest = transport.open(create=True)
        producer = uuid.uuid4().hex
        transport.append_events(producer, 1, 1, [event])

        result = run_request({"action": "exchange", "sync_root": str(self.root)})
        self.assertEqual(result["status"], "received")
        self.journal.bind("share", manifest)
        self.journal.accept("share", producer, result["batch"], result["digest"])
        self.assertEqual(self.journal.checkpoint("share")["cursor"], {producer: 1})

        request_path = self.root / "request.json"
        request_path.write_text(encode({
            "action": "verify_source", "source_root": str(self.root),
            "relative_path": "family.jpg", "expected_hash": photo_hash,
        }), encoding="utf-8")
        process = SharedSyncProcess(publish_capable=False)
        self.addCleanup(process.stop)
        self.assertTrue(process.start([str(request_path)]))
        verification = None
        for _ in range(100):
            verification = process.poll()
            if verification is not None:
                break
            time.sleep(0.025)
        self.assertIsNotNone(verification)
        self.assertEqual(verification.status, "ok")
        persisted = ExchangeJournal(self.journal.path).events("share", producer, 1)[0]
        plan = compare_verified_photo(local, persisted, verified_hash=verification.payload["photo_sha256"])
        self.assertEqual(len(plan.carry_over), 1)
        self.assertEqual(plan.carry_over[0].decision, local_face.decision)
        self.assertEqual(plan.carry_over[0].decision_source, "local")
        self.assertFalse(plan.conflicts)
        with self.assertRaises(ValueError):
            compare_verified_photo(local, {**persisted, "relative_paths": ["other/family.jpg"]}, verified_hash=photo_hash)

        photo_path.write_bytes(b"Different data with the same filename")
        verification = run_request({
            "action": "verify_source", "source_root": str(self.root),
            "relative_path": "family.jpg", "expected_hash": photo_hash,
        })
        self.assertEqual(verification, {"status": "source_changed"})

    def test_acknowledgement_never_replaces_pending_content(self):
        transport = self.transport()
        manifest = transport.open(create=True)
        self.journal.bind("share", manifest)
        self.journal.stage("share", [{"operation": "original"}])
        checkpoint = ExchangeJournal(self.journal.path).checkpoint("share")
        producer = checkpoint["producer_id"]
        wrong_ack = {
            "producer_id": producer, "batch_no": 1, "first_sequence": 1, "last_sequence": 1,
            "previous_digest": None, "events": [{"operation": "different"}],
        }
        with self.assertRaises(SharedStateChanged):
            self.journal.accept("share", producer, wrong_ack, digest(wrong_ack))
        self.assertIsNotNone(self.journal.checkpoint("share")["outgoing"])

        published = run_request({"action": "exchange", "sync_root": str(self.root), "allow_writes": True, **checkpoint})
        resent = run_request({"action": "exchange", "sync_root": str(self.root), "allow_writes": True, **checkpoint})
        self.assertEqual(published, resent)
        self.journal.accept("share", producer, published["batch"], published["digest"])
        checkpoint = self.journal.checkpoint("share")
        self.assertIsNone(checkpoint["outgoing"])
        self.assertEqual(checkpoint["witnesses"][producer]["batch"], 1)

    def test_malformed_events_are_rejected_before_pending_intent(self):
        self.journal.bind("share", {"version": 1, "scope": uuid.uuid4().hex, "generation": uuid.uuid4().hex})
        for events in ([{}] * 129, ["string"], [{"operation": "x" * (256 * 1024)}]):
            with self.assertRaises(ValueError):
                self.journal.stage("share", events)
        self.assertIsNone(self.journal.checkpoint("share")["outgoing"])

    def test_old_witness_cannot_reinitialize_missing_share(self):
        with self.assertRaises(FileNotFoundError):
            run_request({
                "action": "probe", "sync_root": str(self.root), "allow_writes": True,
                "known_manifest": {"version": 1, "scope": uuid.uuid4().hex, "generation": uuid.uuid4().hex},
            })
        self.assertFalse((self.root / ".fm").exists())

    def test_read_only_preview_recovers_after_restore_and_does_not_change_assignments(self):
        data_directory = self.root / "device"
        data_directory.mkdir()
        photo = self.root / "family.jpg"
        photo.write_bytes(b"Photo captured in the previous library")
        photo_hash = hashlib.sha256(photo.read_bytes()).hexdigest()
        with (
            patch.dict(os.environ, FACE_MANAGER_SHARED_LIBRARY_PREVIEW="1"),
            patch.object(schema, "DB_PATH", str(data_directory / "library.sqlite")),
            patch("backend.services.shared_sync_preview.get_data_root", return_value=data_directory),
        ):
            schema.init_db(scan_sources=False)
            with closing(schema.get_conn()) as connection, connection:
                connection.execute("INSERT INTO person(id,name) VALUES(1,'Anna')")
                connection.execute("INSERT INTO cluster(id,person_id) VALUES(1,1)")
                connection.execute(
                    """INSERT INTO image(id,path,directory,filename,content_hash,processed_at,raw_width,raw_height,exif_orientation)
                       VALUES(1,?,?,?,?,?,100,100,1)""",
                    (str(photo), str(self.root), photo.name, photo_hash, "done"),
                )
                connection.execute(
                    "INSERT INTO image_location(image_id,path,directory,filename) VALUES(1,?,?,?)",
                    (str(photo), str(self.root), photo.name),
                )
                connection.execute(
                    """INSERT INTO face(image_id,bbox_x,bbox_y,bbox_w,bbox_h,review_status,cluster_id,embedding)
                       VALUES(1,20,20,40,40,'active',1,?)""",
                    (b"legacy",),
                )
            different = FaceEvidence(
                str(uuid.uuid4()), photo_hash, (0.20, 0.20, 0.60, 0.60),
                Assignment("person", str(uuid.uuid4()), "Bea", provenance="confirmed"), True, True,
            )
            remote = photo_event(SnapshotPhoto(15, photo_hash, (photo.name,), (different,), 100, 100, 1), "PC1")
            transport = self.transport()
            transport.open(create=True)
            transport.append_events(uuid.uuid4().hex, 1, 1, [remote])
            preview = SharedLibraryPreview()
            preview.configure({"source_path": str(self.root), "sync_path": str(self.root)})
            preview.start()
            try:
                ready = self._wait_for_conflict(preview)
                self.assertEqual(ready["status"], "read_only")
                case, = ready["conflicts"]
                self.assertEqual(case["local"]["person_name"], "Anna")
                self.assertEqual(case["shared"]["person_name"], "Bea")
                self.assertFalse(case["can_resolve"])
                self.assertNotIn("photo_id", case)

                preview.library_changed(restoring=True)
                self.assertNotIn("conflicts", preview.snapshot())
                preview.library_changed(restoring=False)
                self.assertEqual(self._wait_for_conflict(preview)["conflict_count"], 1)
            finally:
                preview.stop()
            with closing(schema.get_conn()) as connection:
                self.assertEqual(connection.execute("SELECT name FROM person").fetchone()["name"], "Anna")
                self.assertEqual(connection.execute("SELECT cluster_id FROM face").fetchone()["cluster_id"], 1)

    def _wait_for_conflict(self, preview):
        for _ in range(200):
            state = preview.snapshot()
            if state.get("conflict_count") == 1:
                return state
            time.sleep(0.025)
        self.fail(f"The read-only preview did not produce a conflict: {preview.snapshot()['status']}")

    @unittest.skipIf(os.name == "nt", "Windows junction coverage requires a native share fixture")
    def test_source_root_under_a_symlink_is_rejected(self):
        original = self.root / "private" / "nested"
        original.mkdir(parents=True)
        (original / "photo.jpg").write_bytes(b"private")
        link = self.root / "shared-alias"
        link.symlink_to(original.parent, target_is_directory=True)
        with self.assertRaises((ValueError, OSError, TransportError)):
            run_request({"action": "verify_source", "source_root": str(link / "nested"), "relative_path": "photo.jpg"})
