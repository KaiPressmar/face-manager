import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
import unittest
from unittest.mock import MagicMock, patch

from backend.services.shared_libraries import SharedLibraries
from backend.services.shared_sync_import import ImportShare
from backend.services.shared_sync_preview import SharedLibraryPreview
from backend.services.shared_sync_snapshot import SnapshotPhoto
from backend.services.shared_sync_transport import ShareTransport
from backend.services.shared_sync_worker import run_request


class SharedLibrariesTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.enterContext(patch.dict(os.environ, FACE_MANAGER_SHARED_LIBRARY_PREVIEW="1",
                                     FACE_MANAGER_SHARED_LIBRARY_WRITE_TEST="1"))
        for module in ("shared_libraries", "shared_sync_preview"):
            self.enterContext(patch(f"backend.services.{module}.get_data_root", return_value=self.root))
        self.enterContext(patch.object(SharedLibraryPreview, "start"))
        self.manager = SharedLibraries()
        self.manager.start()
        self.addCleanup(self.manager.stop)
        self.parent = self.root / "Photos"
        self.child = self.parent / "Events"
        self.child.mkdir(parents=True)

    def add(self, root, **options):
        return self.manager.configure({"folder_id": "new", "source_path": str(root), **options})

    def test_child_selected_after_parent_keeps_one_witness_and_removable_shortcut(self):
        parent, = self.add(self.parent)["folders"]
        combined, = self.add(self.child)["folders"]
        self.assertEqual(parent["folder_id"], combined["folder_id"])
        self.assertEqual(combined["linked_paths"], [str(self.child)])
        self.assertTrue(self.manager.contains_source(str(self.child / "test.jpg")))
        state = self.manager.configure({"folder_id": combined["folder_id"], "remove_linked_path": str(self.child)})
        self.assertEqual(state["folders"][0]["linked_paths"], [])
        self.assertTrue(self.manager.contains_source(str(self.child / "test.jpg")))

    def test_uninitialized_child_is_promoted_into_parent_and_survives_restart(self):
        child, = self.add(self.child)["folders"]
        parent, = self.add(self.parent)["folders"]
        self.assertEqual(parent["folder_id"], child["folder_id"])
        self.assertEqual(parent["source_path"], str(self.parent))
        self.assertEqual(parent["linked_paths"], [str(self.child)])
        self.manager.stop()
        self.manager.start()
        self.assertEqual(self.manager.snapshot()["folders"][0]["linked_paths"], [str(self.child)])

    def test_established_child_excluded_from_parent_even_after_child_is_removed(self):
        child, = self.add(self.child)["folders"]
        journal = SharedLibraryPreview._journal()
        journal.bind(SharedLibraryPreview._scope(child), {"version": 1, "scope": "a" * 32, "generation": "b" * 32})
        parent = next(item for item in self.add(self.parent)["folders"] if item["source_path"] == str(self.parent))
        parent_profile = self.manager._profiles[parent["folder_id"]]
        photo = str(self.child / "private.jpg")
        self.assertFalse(parent_profile.contains_source(photo))
        self.assertTrue(self.manager.contains_source(photo))
        self.manager.configure({"folder_id": child["folder_id"], "source_path": None})
        self.assertFalse(self.manager.contains_source(photo))
        self.manager.stop()
        self.manager.start()
        self.assertFalse(self.manager.contains_source(photo))
        self.assertEqual(self.add(self.child, sync_path=str(self.parent))["folders"][0]["linked_paths"], [str(self.child)])
        self.assertTrue(self.manager.contains_source(photo))

    def test_existing_independent_child_manifest_is_not_linked_by_defaults(self):
        transport = ShareTransport(self.child, allow_writes=True)
        if not transport.probe_writes():
            self.skipTest("Local filesystem does not support no-replace publication")
        transport.open(create=True)
        parent, = self.add(self.parent, write_enabled=True)["folders"]
        joined = self.add(self.child, write_enabled=True)["folders"]
        self.assertEqual(len(joined), 2)
        self.assertEqual(joined[0]["linked_paths"], [])
        self.assertFalse(self.manager._profiles[parent["folder_id"]].contains_source(str(self.child / "private.jpg")))

    def test_duplicate_manifest_cannot_be_claimed_by_two_local_profiles(self):
        first, = self.add(self.parent)["folders"]
        second = self.add(self.root / "Alternative")["folders"][1]
        manifest = {"version": 1, "scope": "d" * 32, "generation": "e" * 32}
        self.assertTrue(self.manager._profiles[first["folder_id"]]._claim_manifest(manifest))
        self.assertFalse(self.manager._profiles[second["folder_id"]]._claim_manifest(manifest))
        SharedLibraryPreview._journal().bind(SharedLibraryPreview._scope(first), manifest)
        self.manager.stop()
        self.manager.start()
        self.assertFalse(self.manager._profiles[second["folder_id"]]._claim_manifest(manifest))
        self.assertTrue(self.manager._profiles[first["folder_id"]]._claim_manifest(manifest))

    def test_existing_parent_manifest_prevents_creating_nested_index(self):
        transport = ShareTransport(self.parent, allow_writes=True)
        if not transport.probe_writes():
            self.skipTest("Local filesystem does not support no-replace publication")
        transport.open(create=True)
        result = run_request({
            "action": "probe", "sync_root": str(self.child), "allow_writes": True,
            "initialize_share": True, "find_parent": True,
        })
        self.assertEqual(result, {"status": "parent_share", "parent": str(self.parent)})
        self.assertFalse((self.child / ".fm").exists())

    def test_conflict_route_and_retry_revision_are_bound_to_folder(self):
        first, = self.add(self.parent)["folders"]
        second = self.add(self.root / "Alternative")["folders"][1]
        profiles = [self.manager._profiles[folder["folder_id"]] for folder in (first, second)]
        for profile in profiles:
            profile._status = {"status": "online", "conflict_count": 1,
                               "conflicts": [{"id": "same-local-id", "revision": "revision"}]}
        cases = self.manager.snapshot()["conflicts"]
        self.assertNotEqual(cases[0]["id"], cases[1]["id"])
        with patch.object(profiles[1], "select_conflict") as select:
            self.manager.select_conflict(cases[1]["id"], "revision", "shared")
            select.assert_called_once_with("same-local-id", "revision", "shared")

    def test_restore_waits_for_guarded_analysis_i_o_and_revokes_new_requests(self):
        manifest = {"version": 1, "scope": "a" * 32, "generation": "b" * 32}
        with patch.dict(os.environ, FACE_MANAGER_SHARED_LIBRARY_ANALYSIS_TEST="1"):
            state, = self.add(self.parent, write_enabled=True, share_embeddings=True)["folders"]
            profile = self.manager._profiles[state["folder_id"]]
            profile._analysis_share = ImportShare(str(self.parent), str(self.parent), manifest, "a" * 32, "b" * 32)
            binding = profile.analysis_share()
            in_worker, finished = Event(), Event()

            def request(*args, **kwargs):
                in_worker.set()
                finished.wait(5)
                return {"status": "job"}

            lane = MagicMock()
            lane.process.busy = False
            lane.request.side_effect = request
            payload = {"action": "job", "operation": "finish_result", "allow_writes": True}
            task = Thread(target=self.manager.dispatch_analysis, args=(binding, lane, payload))
            task.start()
            try:
                self.assertTrue(in_worker.wait(2))
                self.assertFalse(self.manager.begin_restore())
            finally:
                finished.set()
                task.join(5)
            self.assertTrue(self.manager.begin_restore())
            self.assertEqual(self.manager.dispatch_analysis(binding, lane, payload), {"status": "unavailable"})

    def test_auto_application_uses_current_photo_locations_after_verification(self):
        photo_hash = "a" * 64
        expected = SnapshotPhoto(1, photo_hash, ("plain.jpg",), (), 100, 100, 1)
        out_of_scope = SnapshotPhoto(1, photo_hash, ("plain.jpg", "Events/private.jpg"), (), 100, 100, 1)
        profile = SharedLibraryPreview()
        profile.set_exclusions((str(self.child),))
        journal = MagicMock()
        journal.latest_photo_positions.return_value = {0}
        journal.events.return_value = [{"photo_sha256": photo_hash, "relative_paths": ["plain.jpg"]}]
        database = MagicMock()
        database.execute.return_value.fetchone.return_value = {"id": 1}
        with (
            patch("backend.services.shared_sync_preview.get_conn", return_value=database),
            patch("backend.services.shared_sync_preview.capture_image", side_effect=[expected, out_of_scope]),
            patch("backend.services.shared_sync_preview.apply_unambiguous") as apply,
            patch.object(profile, "_request", return_value={"status": "verified", "photo_sha256": photo_hash}),
        ):
            profile._review(journal, "scope", "a" * 32, 1, str(self.parent), {}, {}, "b" * 32, profile._revision,
                            decision_scope="scope", apply=True, stats={})
        apply.assert_not_called()
