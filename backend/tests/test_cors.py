import os
import unittest
from unittest.mock import patch

from fastapi import FastAPI

from backend.app import configure_cors


class CorsTest(unittest.IsolatedAsyncioTestCase):
    async def request(self, *, origin=None, method="GET", allowed_origins="", platform=""):
        application = FastAPI()

        @application.get("/api/map/points")
        async def map_points():
            return {"points": []}

        with patch.dict(os.environ, {
            "FACE_MANAGER_PLATFORM": platform,
            "FACE_MANAGER_ALLOWED_ORIGINS": allowed_origins,
        }):
            configure_cors(application)

        headers = [] if origin is None else [(b"origin", origin.encode())]
        if method == "OPTIONS":
            headers.extend([
                (b"access-control-request-method", b"GET"),
                (b"access-control-request-headers", b"x-face-manager-display-platform"),
            ])
        scope = {
            "type": "http",
            "method": method,
            "path": "/api/map/points",
            "headers": headers,
            "query_string": b"",
        }
        messages = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        await application(scope, receive, send)
        return messages[0]["status"], dict(messages[0]["headers"])

    async def test_default_serves_local_requests_without_granting_cross_origin_reads(self):
        self.assertEqual((await self.request())[0], 200)
        status, headers = await self.request(origin="https://untrusted.example")
        self.assertEqual(status, 200)
        self.assertNotIn(b"access-control-allow-origin", headers)
        status, _ = await self.request(method="OPTIONS", origin="https://untrusted.example")
        self.assertEqual(status, 400)

    async def test_only_exact_configured_origins_are_allowed(self):
        configured = "*, https://photos.example, http://localhost:5173, https://*.example"
        for origin in ("https://photos.example", "http://localhost:5173"):
            with self.subTest(origin=origin):
                status, headers = await self.request(
                    method="OPTIONS", origin=origin, allowed_origins=configured,
                )
                self.assertEqual(status, 200)
                self.assertEqual(headers[b"access-control-allow-origin"], origin.encode())

        for origin in ("https://other.example", "http://localhost:5174"):
            with self.subTest(origin=origin):
                _, headers = await self.request(origin=origin, allowed_origins=configured)
                self.assertNotIn(b"access-control-allow-origin", headers)

    async def test_android_ignores_configured_cross_origin_access(self):
        origin = "http://localhost:5173"
        _, headers = await self.request(origin=origin, allowed_origins=origin, platform="android")
        self.assertNotIn(b"access-control-allow-origin", headers)
