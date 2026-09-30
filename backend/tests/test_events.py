import asyncio
import json
import time
import unittest

from backend.services.events import EventHub, TrailingThrottle, format_sse


class TrailingThrottleTest(unittest.TestCase):
    def test_first_call_runs_immediately(self):
        calls = []
        throttle = TrailingThrottle(lambda: calls.append(time.monotonic()), interval=0.05)

        throttle()

        self.assertEqual(len(calls), 1)

    def test_burst_coalesces_into_one_trailing_run(self):
        calls: list[int] = []
        counter = {"value": 0}

        def record() -> None:
            # Reads live state at invocation time, like the real snapshot publish.
            calls.append(counter["value"])

        throttle = TrailingThrottle(record, interval=0.05)

        throttle()  # runs now (state 0)
        for value in range(1, 6):
            counter["value"] = value
            throttle()  # all within the cool-down: coalesced

        # Wait past the interval so the single trailing run fires.
        deadline = time.monotonic() + 1.0
        while len(calls) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)

        self.assertEqual(len(calls), 2, "one immediate run plus one trailing run")
        self.assertEqual(calls[0], 0)
        self.assertEqual(calls[1], 5, "the trailing run reflects the latest state")

    def test_flush_cancels_pending_trailing_run(self):
        calls = []
        throttle = TrailingThrottle(lambda: calls.append(1), interval=0.2)

        throttle()  # immediate
        throttle()  # schedules a trailing run
        throttle.flush()
        time.sleep(0.3)

        self.assertEqual(len(calls), 1, "flushed trailing run must not fire")

    def test_callback_error_does_not_propagate(self):
        def boom() -> None:
            raise RuntimeError("subscriber failed")

        throttle = TrailingThrottle(boom, interval=0.01)

        # Must not raise despite the callback throwing.
        throttle()


class FormatSseTest(unittest.TestCase):
    def test_frame_shape(self):
        frame = format_sse("imports", {"queued_count": 2})
        self.assertEqual(
            frame,
            'event: imports\ndata: {"queued_count": 2}\n\n',
        )

    def test_non_json_native_values_are_stringified(self):
        # ``default=str`` keeps unusual payloads from raising during serialize.
        frame = format_sse("clusters", {"reason": {"nested"}})
        self.assertTrue(frame.startswith("event: clusters\ndata: "))


class EventHubTest(unittest.TestCase):
    def test_publish_before_loop_bound_is_noop(self):
        hub = EventHub()
        # No loop bound yet; publish must not raise and just records latest.
        hub.publish("imports", {"a": 1})
        self.assertEqual(hub._latest["imports"], {"a": 1})

    def test_subscriber_receives_latest_snapshot_on_connect(self):
        async def scenario():
            hub = EventHub()
            hub.bind_loop(asyncio.get_running_loop())
            hub.publish("imports", {"running_count": 1})
            hub.publish("clusters", {"reason": "assign_person"})

            stream = hub.stream()
            first = await asyncio.wait_for(stream.__anext__(), timeout=1)
            second = await asyncio.wait_for(stream.__anext__(), timeout=1)
            await stream.aclose()
            return [first, second]

        frames = asyncio.run(scenario())
        topics = {frame.split("\n", 1)[0] for frame in frames}
        self.assertEqual(topics, {"event: imports", "event: clusters"})

    def test_publish_reaches_connected_subscriber(self):
        async def scenario():
            hub = EventHub()
            hub.bind_loop(asyncio.get_running_loop())

            stream = hub.stream()
            # Drain any initial frames (none yet, so this would block) — instead
            # publish first so there is exactly one pending frame to read.
            hub.publish("autocluster", {"task": {"status": "running"}})
            frame = await asyncio.wait_for(stream.__anext__(), timeout=1)
            await stream.aclose()
            return frame

        frame = asyncio.run(scenario())
        header, body = frame.split("\n")[:2]
        self.assertEqual(header, "event: autocluster")
        self.assertEqual(
            json.loads(body[len("data: ") :]),
            {"task": {"status": "running"}},
        )

    def test_coalesces_to_latest_per_topic(self):
        async def scenario():
            hub = EventHub()
            hub.bind_loop(asyncio.get_running_loop())

            stream = hub.stream()
            # Several rapid updates before the stream is read must collapse to
            # only the most recent payload for the topic.
            hub.publish("imports", {"n": 1})
            hub.publish("imports", {"n": 2})
            hub.publish("imports", {"n": 3})
            await asyncio.sleep(0)  # let scheduled deliveries run
            frame = await asyncio.wait_for(stream.__anext__(), timeout=1)
            await stream.aclose()
            return frame

        frame = asyncio.run(scenario())
        body = frame.split("\n")[1]
        self.assertEqual(json.loads(body[len("data: ") :]), {"n": 3})

    def test_unsubscribe_on_stream_close(self):
        async def scenario():
            hub = EventHub()
            hub.bind_loop(asyncio.get_running_loop())
            stream = hub.stream()
            hub.publish("imports", {"n": 1})
            await asyncio.wait_for(stream.__anext__(), timeout=1)
            self.assertEqual(len(hub._subscribers), 1)
            await stream.aclose()
            return len(hub._subscribers)

        remaining = asyncio.run(scenario())
        self.assertEqual(remaining, 0)


if __name__ == "__main__":
    unittest.main()
