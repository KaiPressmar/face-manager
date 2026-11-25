import os
import runpy
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from backend.services import shared_sync_io
from backend.services.shared_sync_io import SharedSyncProcess, SharedSyncResult


class SharedSyncProcessTests(unittest.TestCase):
    def test_real_child_replies_via_file_with_no_output_pipe(self):
        def command(args, reply):
            script = (
                "import pathlib, sys; "
                "print('diagnostic' * 200_000); "
                "pathlib.Path(sys.argv[1]).write_bytes(b'{\"done\":true}')"
            )
            return [sys.executable, "-c", script, str(reply)], {}

        supervisor = SharedSyncProcess(timeout_seconds=10)
        self.addCleanup(supervisor.stop)
        with patch.object(shared_sync_io, "_worker_command", side_effect=command):
            self.assertTrue(supervisor.start(["unused"]))
            self.assertFalse(supervisor.start([]))
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = supervisor.poll()
            if result is not None:
                break
            time.sleep(0.01)
        else:
            self.fail("The worker did not finish")
        self.assertEqual(result, SharedSyncResult("ok", {"done": True}))
        self.assertIsNone(supervisor.poll())
        self.assertFalse(supervisor.busy)

    def test_timeout_reports_unknown_and_refuses_more_workers_until_reap(self):
        supervisor = SharedSyncProcess(timeout_seconds=5)
        stuck = Mock()
        stuck.poll.return_value = None
        now = 100.0

        def clock():
            return now

        def sleep(seconds):
            nonlocal now
            now += seconds

        with (
            patch.object(shared_sync_io.subprocess, "Popen", return_value=stuck) as spawn,
            patch.object(shared_sync_io.time, "monotonic", side_effect=clock),
            patch.object(shared_sync_io.time, "sleep", side_effect=sleep),
        ):
            self.assertTrue(supervisor.start(["publish"]))
            self.assertIsNone(supervisor.poll())
            now += 5
            self.assertEqual(supervisor.poll().status, "published_unknown")
            self.assertTrue(supervisor.busy)
            self.assertFalse(supervisor.start(["publish-again"]))
            self.assertFalse(supervisor.stop(timeout_seconds=30))
            self.assertAlmostEqual(now, 107.0)
            stuck.kill.assert_called_once()
            spawn.assert_called_once()
            stuck.poll.return_value = -9
            self.assertIsNone(supervisor.poll())
            self.assertFalse(supervisor.busy)

    def test_shutdown_preserves_unknown_result_until_consumed(self):
        supervisor = SharedSyncProcess()
        process = Mock()
        process.poll.side_effect = [None, -9]
        with patch.object(shared_sync_io.subprocess, "Popen", return_value=process):
            self.assertTrue(supervisor.start(["publish"]))
            self.assertFalse(supervisor.stop(timeout_seconds=0))
            self.assertTrue(supervisor.stop())
            self.assertFalse(supervisor.start(["publish-again"]))
            self.assertEqual(supervisor.poll().status, "published_unknown")
            self.assertFalse(supervisor.busy)

    def test_read_only_crash_and_invalid_json_are_errors(self):
        cases = (
            (1, b'{"status":"ok"}'),
            (0, b'[]'),
            (0, b'{"key":"\xff"}'),
            (0, b'{"value":"' + b"x" * shared_sync_io._MAX_RESULT_BYTES + b'"}'),
        )
        for exit_code, data in cases:
            with self.subTest(exit_code=exit_code, length=len(data)):
                supervisor = SharedSyncProcess(publish_capable=False)
                process = Mock()
                process.poll.return_value = exit_code
                with patch.object(shared_sync_io.subprocess, "Popen", return_value=process) as spawn:
                    self.assertTrue(supervisor.start(["read"]))
                    reply = Path(spawn.call_args.args[0][-1])
                    reply.write_bytes(data)
                    self.assertEqual(supervisor.poll().status, "error")
                    self.assertFalse(reply.parent.exists())

    def test_spawn_failure_is_an_error_without_quarantine(self):
        supervisor = SharedSyncProcess()
        with patch.object(shared_sync_io.subprocess, "Popen", side_effect=OSError("spawn failed")) as spawn:
            self.assertFalse(supervisor.start(["publish"]))
            self.assertFalse(supervisor.busy)
            self.assertEqual(supervisor.poll().status, "error")
            self.assertFalse(Path(spawn.call_args.args[0][-1]).parent.exists())

    def test_frozen_worker_uses_same_executable_and_resets_bootloader(self):
        reply = Path("a directory") / "reply.json"
        with patch.object(sys, "frozen", True, create=True):
            command, options = shared_sync_io._worker_command(["request path"], reply)
        self.assertEqual(command, [sys.executable, "--shared-sync-worker", "request path", os.fspath(reply)])
        self.assertEqual(options["env"]["PYINSTALLER_RESET_ENVIRONMENT"], "1")

    def test_frozen_entrypoint_dispatches_without_importing_the_webview(self):
        worker = SimpleNamespace(main=Mock(return_value=7))
        entrypoint = Path(__file__).resolve().parents[1] / "desktop_main.py"
        modules = {"backend.services.shared_sync_worker": worker, "webview": None, "uvicorn": None}
        args = ["FaceManager.exe", "--shared-sync-worker", "request path", "result path"]
        with (
            patch.object(sys, "frozen", True, create=True),
            patch.object(sys, "argv", args),
            patch.dict(sys.modules, modules),
            self.assertRaises(SystemExit) as stopped,
        ):
            runpy.run_path(str(entrypoint), run_name="__main__")
        self.assertEqual(stopped.exception.code, 7)
        worker.main.assert_called_once_with(["request path", "result path"])
