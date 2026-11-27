import unittest
from email.message import Message
from gzip import compress
from io import BytesIO
from unittest.mock import patch
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit

from fastapi import HTTPException

from backend.app import get_map_detail_tile
from backend.services import map_detail_tiles as tiles


class ProviderResponse(BytesIO):
    status = 200

    def __init__(self, body: bytes, media_type: str, **headers: str):
        super().__init__(body)
        self.headers = Message()
        self.headers["Content-Type"] = media_type
        for name, value in headers.items():
            self.headers[name] = value


class MapDetailTilesTest(unittest.TestCase):
    @patch.object(tiles, "build_opener")
    def test_raster_tiles_get_the_expected_geographic_bbox_and_safe_headers(self, opener):
        jpeg = b"\xff\xd8\xffpreview"
        opener.return_value.open.return_value = ProviderResponse(jpeg, "image/jpeg", **{"Cache-Control": "public, max-age=1800"})

        result = get_map_detail_tile("satellite", 2, 4, 1)
        self.assertEqual(result.body, jpeg)
        self.assertEqual(result.headers["Content-Type"], "image/jpeg")
        self.assertEqual(result.headers["Cache-Control"], "private, max-age=1800")
        self.assertEqual(result.headers["X-Content-Type-Options"], "nosniff")
        request = opener.return_value.open.call_args.args[0]
        url = urlsplit(request.full_url)
        self.assertEqual((url.scheme, url.netloc, url.path), ("https", "tiles.maps.eox.at", "/wms"))
        query = parse_qs(url.query)
        self.assertEqual(query["LAYERS"], ["s2cloudless"])
        self.assertEqual(query["SRS"], ["EPSG:4326"])
        self.assertEqual(query["BBOX"], ["0,0,45,45"])
        self.assertEqual(query["WIDTH"], ["256"])
        self.assertEqual({key.lower() for key, _ in request.header_items()}, {"user-agent", "accept", "accept-encoding"})
        self.assertEqual(opener.return_value.open.call_args.kwargs["timeout"], 5)
        redirect_handler = opener.call_args.args[0]
        self.assertIsNone(redirect_handler.redirect_request(None, None, 302, "Found", {}, "https://elsewhere.invalid"))

    @patch.object(tiles, "build_opener")
    def test_vector_gzip_is_decompressed_with_a_size_limit(self, opener):
        vector_bytes = b"\x1a\x03map"
        provider = opener.return_value.open
        provider.return_value = ProviderResponse(compress(vector_bytes), "application/x-protobuf", **{
            "Content-Encoding": "gzip", "Cache-Control": "no-store",
        })
        result = get_map_detail_tile("street", 14, 8192, 4000)
        self.assertEqual(result.body, vector_bytes)
        self.assertEqual(result.headers["content-type"], "application/vnd.mapbox-vector-tile")
        self.assertNotIn("content-encoding", result.headers)
        self.assertEqual(result.headers["cache-control"], "private, no-store")
        self.assertEqual(provider.call_args.args[0].full_url, "https://tiles.openfreemap.org/planet/latest/14/8192/4000.pbf")

        provider.return_value = ProviderResponse(compress(b"x" * 129), "application/x-protobuf", **{"Content-Encoding": "gzip"})
        with patch.object(tiles, "_MAX_TILE_BYTES", 128), self.assertRaises(HTTPException) as failure:
            get_map_detail_tile("street", 14, 8192, 4000)
        self.assertEqual(failure.exception.status_code, 502)

    @patch.object(tiles, "build_opener")
    def test_invalid_tiles_and_capacity_rejection_never_contact_provider(self, opener):
        for args in (("street", 14, 16384, 0), ("satellite", 14, 0, 0), ("landscape", 14, 0, 16384),
                     ("landscape", 14, -1, 0), ("absolute-url", 0, 0, 0)):
            with self.subTest(args=args), self.assertRaises(HTTPException) as failure:
                get_map_detail_tile(*args)
            self.assertEqual(failure.exception.status_code, 422)
        with patch.object(tiles, "_SLOTS") as slots, self.assertRaises(HTTPException) as failure:
            slots.acquire.return_value = False
            get_map_detail_tile("landscape", 14, 0, 0)
        self.assertEqual(failure.exception.status_code, 503)
        opener.assert_not_called()

    @patch.object(tiles, "build_opener")
    def test_provider_failures_leave_the_relay_available_for_the_next_request(self, opener):
        provider = opener.return_value.open
        for response in [
            ProviderResponse(b"<html>error</html>", "text/html"),
            ProviderResponse(b"<xml>error</xml>", "image/jpeg"),
            ProviderResponse(b"data", "image/jpeg", **{"Content-Encoding": "br"}),
            ProviderResponse(b"x" * (tiles._MAX_TILE_BYTES + 1), "image/jpeg"),
        ]:
            provider.return_value = response
            with self.assertRaises(HTTPException) as failure:
                get_map_detail_tile("landscape", 14, 0, 0)
            self.assertEqual(failure.exception.status_code, 502)
        provider.side_effect = URLError("timeout")
        with self.assertRaises(HTTPException) as failure:
            get_map_detail_tile("landscape", 14, 0, 0)
        self.assertEqual(failure.exception.status_code, 502)
        provider.side_effect = None
        provider.return_value = ProviderResponse(b"\xff\xd8\xffpreview", "image/jpeg")
        self.assertEqual(get_map_detail_tile("landscape", 14, 0, 0).status_code, 200)
        self.assertEqual(parse_qs(urlsplit(provider.call_args.args[0].full_url).query)["LAYERS"], ["terrain-light"])
