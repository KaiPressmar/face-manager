import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
from threading import Barrier
import unittest
from unittest.mock import patch
from uuid import uuid4

from backend.services import shared_sync_transport as sync


class SharedSyncTransportTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.transport = sync.ShareTransport(self.root, allow_writes=True)
        if not self.transport.probe_writes():
            self.skipTest("Local filesystem has no supported no-replace publication")
        self.manifest = self.transport.open(create=True)

    def test_read_only_open_validates_scope_without_writing(self):
        reader = sync.ShareTransport(self.root)
        self.assertEqual(reader.open(), self.manifest)
        self.assertFalse(reader.probe_writes())
        with self.assertRaises(sync.ReadOnlyTransportError):
            reader.put_object("snapshot", {"test": True})
        self.assertEqual(list((self.root / ".fm").iterdir()), [self.root / ".fm" / "manifest.json"])

    def test_two_initializers_share_one_immutable_manifest(self):
        other = self.root / "other"
        other.mkdir()
        wait = Barrier(2)

        def initialize():
            transport = sync.ShareTransport(other, allow_writes=True)
            wait.wait(5)
            return transport.open(create=True)

        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(*list(pool.map(lambda _: initialize(), range(2))))

    def test_two_initializers_survive_a_mount_that_ignores_no_replace(self):
        other = self.root / "other"
        other.mkdir()
        ready_to_rename = Barrier(2)

        def lossy_rename(directory, source, destination):
            if destination == "manifest.json":
                ready_to_rename.wait(5)
            os.rename(
                directory.target(source), directory.target(destination),
                src_dir_fd=directory.fd, dst_dir_fd=directory.fd,
            )

        def initialize():
            return sync.ShareTransport(other, allow_writes=True).open(create=True)

        with patch.object(sync, "_rename_no_replace", side_effect=lossy_rename):
            with ThreadPoolExecutor(2) as pool:
                first, second = list(pool.map(lambda _: initialize(), range(2)))
        self.assertEqual(first, second)
        self.assertEqual(sync.ShareTransport(other).open(), first)
        self.assertEqual([path.name for path in (other / ".fm").iterdir()], ["manifest.json"])

    def test_invalid_existing_slots_cannot_be_replaced(self):
        for flavor in ("empty", "file", "invalid", "additional-entry"):
            with self.subTest(flavor=flavor):
                root = self.root / flavor
                slot = root / ".fm" / "manifest.json"
                slot.parent.mkdir(parents=True)
                if flavor == "file":
                    slot.write_bytes(b"original")
                else:
                    slot.mkdir()
                    if flavor == "invalid":
                        (slot / "payload.json").write_bytes(b"original")
                    if flavor == "additional-entry":
                        (slot / "payload.json").write_bytes(sync._canonical(self.manifest))
                        (slot / "extra.txt").write_bytes(b"original")
                with patch.object(sync, "_rename_no_replace") as rename:
                    with self.assertRaises(sync.InvalidTransportData):
                        sync.ShareTransport(root, allow_writes=True).open(create=True)
                rename.assert_not_called()
                self.assertTrue(slot.exists())

    def test_bounded_content_addresses_and_context(self):
        payload = {"title": "Urlaub", "numbers": [1, 2, 3]}
        digest = self.transport.put_object("snapshot", payload)
        self.assertEqual(self.transport.put_object("snapshot", payload), digest)
        self.assertEqual(self.transport.get_object("snapshot", digest), payload)
        with self.assertRaises(sync.InvalidTransportData):
            self.transport.put_object("../escape", {})
        with self.assertRaises(sync.InvalidTransportData):
            self.transport.put_object("snapshot", {"title": "x" * sync.MAX_RECORD_BYTES})
        with self.assertRaises(sync.InvalidTransportData):
            self.transport.put_object("snapshot", {"x": float("nan")})
        path = self.root / ".fm" / "objects" / "snapshot" / digest[:2] / f"{digest}.json" / "payload.json"
        path.write_bytes(path.read_bytes().replace(b"Urlaub", b"Stadt"))
        with self.assertRaises(sync.InvalidTransportData):
            self.transport.get_object("snapshot", digest)

    def test_generation_change_and_symlinked_sidecar_are_rejected(self):
        old = self.root / ".fm"
        old.rename(self.root / ".fm-old")
        other = sync.ShareTransport(self.root, allow_writes=True)
        other.open(create=True)
        with self.assertRaises(sync.InvalidTransportData):
            self.transport.put_object("snapshot", {})
        new = self.root / ".fm"
        new.rename(self.root / ".fm-new")
        try:
            new.symlink_to(old.with_name(".fm-old"), target_is_directory=True)
        except OSError:
            self.skipTest("Directory symlinks are unavailable")
        with self.assertRaises((OSError, sync.InvalidTransportData)):
            sync.ShareTransport(self.root).open()

    def test_publication_does_not_poison_a_replaced_share_generation(self):
        first = self.root / ".fm"
        first.rename(self.root / ".fm-old")
        replacement = sync.ShareTransport(self.root, allow_writes=True)
        replacement.open(create=True)
        with self.assertRaises(sync.InvalidTransportData):
            self.transport.append_events(uuid4().hex, 1, 1, [{"name": "old"}])
        self.assertFalse((first / "events").exists())
        self.assertEqual(replacement.list_producers(), [])

    def test_event_batch_gap_conflict_replay_and_scope(self):
        producer = uuid4().hex
        with self.assertRaises(sync.InvalidTransportData):
            self.transport.append_events(producer, 2, 2, [{"type": "test"}])
        with self.assertRaises(sync.InvalidTransportData):
            self.transport.append_events(producer, 1, 2, [{"type": "test"}])
        events = [{"name": "A"}, {"name": "B"}]
        batch = self.transport.append_events(producer, 1, 1, events)
        self.assertEqual(self.transport.append_events(producer, 1, 1, events), batch)
        with self.assertRaises(sync.PublishConflictError):
            self.transport.append_events(producer, 1, 1, [{"name": "other"}])
        second = self.transport.append_events(producer, 2, 3, [{"name": "C"}])
        self.assertEqual(second["last_sequence"], 3)
        self.assertEqual(self.transport.get_event_batch(producer, 2), second)
        self.assertEqual(self.transport.list_producers(), [producer])
        self.assertIsNone(self.transport.get_event_batch(producer, 3))

    def test_bounded_audit_detects_prefix_gap_despite_intact_head(self):
        producer = uuid4().hex
        for number in range(1, 4):
            self.transport.append_events(producer, number, number, [{"index": number}])
        with patch.object(self.transport, "_directory_names", side_effect=AssertionError("unexpected listing")):
            self.assertEqual(
                self.transport.audit_event_stream(producer, limit=2),
                {"after_batch": 0, "last_batch": 2, "at_tail": False},
            )
        self.assertEqual(
            self.transport.audit_event_stream(producer, after_batch=2),
            {"after_batch": 2, "last_batch": 3, "at_tail": True},
        )
        first = self.root.joinpath(".fm", *self.transport._event_directory(producer, 1), f"{1:020}.json")
        (first / "payload.json").unlink()
        first.rmdir()
        self.assertIsNotNone(self.transport.get_event_batch(producer, 3))
        with self.assertRaises(sync.InvalidTransportData):
            self.transport.audit_event_stream(producer)

    def test_audit_detects_gap_before_later_shard_and_rechecks_concurrent_commit(self):
        producer = uuid4().hex
        with patch.object(sync, "_EVENTS_PER_SHARD", 2):
            for number in range(1, 4):
                self.transport.append_events(producer, number, number, [{}])
            real_read = self.transport._read_event_batch
            missed = False

            def stale_lookup(device, number):
                nonlocal missed
                if number == 1 and not missed:
                    missed = True
                    return None
                return real_read(device, number)

            with patch.object(self.transport, "_read_event_batch", side_effect=stale_lookup):
                self.assertEqual(self.transport.audit_event_stream(producer)["last_batch"], 3)
            first_shard = self.root.joinpath(".fm", *self.transport._event_directory(producer, 1))
            for path in first_shard.iterdir():
                (path / "payload.json").unlink()
                path.rmdir()
            first_shard.rmdir()
            with self.assertRaises(sync.InvalidTransportData):
                self.transport.audit_event_stream(producer)

    def test_event_limit_is_separate_from_content_object_limit(self):
        producer = uuid4().hex
        margin = len(sync._canonical([{"value": ""}]))
        text = "x" * (sync.MAX_EVENT_BATCH_BYTES - margin)
        self.transport.append_events(producer, 1, 1, [{"value": text}])
        oversized_event = {"value": text + "x"}
        with self.assertRaises(sync.InvalidTransportData):
            self.transport.append_events(producer, 2, 2, [oversized_event])
        digest = self.transport.put_object("analysis", oversized_event)
        self.assertEqual(self.transport.get_object("analysis", digest), oversized_event)

    def test_event_batch_lost_rename_reply_can_only_advance_after_readback(self):
        producer = uuid4().hex
        original = sync._rename_no_replace

        def publish_then_fail(*args):
            original(*args)
            raise OSError("lost response")

        with patch.object(sync, "_rename_no_replace", side_effect=publish_then_fail):
            committed = self.transport.append_events(producer, 1, 1, [{"name": "A"}])
        self.assertEqual(self.transport.get_event_batch(producer, 1), committed)

        with patch.object(sync, "_rename_no_replace", side_effect=OSError("no response")):
            with self.assertRaises(sync.UnknownPublishError):
                self.transport.append_events(producer, 2, 2, [{"name": "B"}])
        self.assertIsNone(self.transport.get_event_batch(producer, 2))
        self.assertEqual(self.transport.append_events(producer, 2, 2, [{"name": "B"}])["last_sequence"], 2)

    def test_nominal_rename_without_the_final_slot_is_unknown(self):
        producer = uuid4().hex
        with patch.object(sync, "_rename_no_replace", return_value=None):
            with self.assertRaises(sync.UnknownPublishError):
                self.transport.append_events(producer, 1, 1, [{"name": "A"}])
        self.assertIsNone(self.transport.get_event_batch(producer, 1))
        event_directory = self.root.joinpath(".fm", *self.transport._event_directory(producer, 1))
        stage, = event_directory.iterdir()
        self.assertTrue(stage.name.startswith(".pending-"))
        self.assertTrue((stage / "payload.json").is_file())

    def test_attempt_owner_and_expiry_are_fenced_through_the_same_slot(self):
        job, device, session = (uuid4().hex for _ in range(3))
        claim = self.transport.claim_attempt(job, 1, device, session)
        self.assertEqual(self.transport.get_claim(job, 1), claim)
        with self.assertRaises(sync.PublishConflictError):
            self.transport.claim_attempt(job, 1, uuid4().hex, session)
        with self.assertRaises(sync.InvalidTransportData):
            self.transport.claim_attempt(job, 2, device, session)
        self.transport.write_heartbeat(job, 1, device, session, 10)
        with self.assertRaises(sync.ReadOnlyTransportError):
            self.transport.observe_heartbeat(job, 1)
        with patch.object(sync.time, "monotonic", return_value=100):
            first = self.transport.observe_heartbeat(job, 1, server_fresh=True)
        with patch.object(sync.time, "monotonic", return_value=159):
            with self.assertRaises(sync.PublishConflictError):
                self.transport.expire_attempt(job, 1, first, server_fresh=True)
        self.transport.write_heartbeat(job, 1, device, session, 11)
        with patch.object(sync.time, "monotonic", return_value=170):
            with self.assertRaises(sync.PublishConflictError):
                self.transport.expire_attempt(job, 1, first, server_fresh=True)
            renewed = self.transport.observe_heartbeat(job, 1, first, server_fresh=True)
        with patch.object(sync.time, "monotonic", return_value=231):
            self.transport.expire_attempt(job, 1, renewed, server_fresh=True)
        result = self.transport.put_object("analysis", {"job_id": job, "faces": []})
        self.transport.claim_attempt(job, 2, device, session)
        with self.assertRaises(sync.PublishConflictError):
            self.transport.finish_attempt(job, 1, device, session, result)
        self.transport.finish_attempt(job, 2, device, session, result)
        self.assertEqual(self.transport.get_decision(job, 2)["result_digest"], result)
        with self.assertRaises(sync.InvalidTransportData):
            self.transport.claim_attempt(job, 3, device, session)

    def test_expiry_and_completion_race_has_exactly_one_winner(self):
        job, device, session = (uuid4().hex for _ in range(3))
        self.transport.claim_attempt(job, 1, device, session)
        result = self.transport.put_object("analysis", {"job_id": job, "faces": []})
        with patch.object(sync.time, "monotonic", return_value=100):
            observation = self.transport.observe_heartbeat(job, 1, server_fresh=True)
        race = Barrier(2)

        def compete(action):
            transport = sync.ShareTransport(self.root, allow_writes=True)
            transport.open()
            race.wait(5)
            try:
                if action == "expired":
                    transport.expire_attempt(job, 1, observation, server_fresh=True)
                else:
                    transport.finish_attempt(job, 1, device, session, result)
                return action
            except sync.PublishConflictError:
                return None

        with patch.object(sync.time, "monotonic", return_value=170):
            with ThreadPoolExecutor(2) as pool:
                outcomes = list(pool.map(compete, ("expired", "completed")))
        self.assertEqual(outcomes.count(None), 1)
        self.assertIn(self.transport.get_decision(job, 1)["state"], outcomes)

    def test_linked_records_are_never_read_as_share_data(self):
        entry = self.root / ".fm" / "manifest.json" / "payload.json"
        original = self.root / "original.json"
        entry.rename(original)
        try:
            os.link(original, entry)
        except OSError:
            self.skipTest("Hard links are unavailable")
        with self.assertRaises(sync.InvalidTransportData):
            self.transport.open()
