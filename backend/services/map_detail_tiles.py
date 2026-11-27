"""Fetch a single visible map tile from a fixed provider."""

from dataclasses import dataclass
from gzip import GzipFile
from http.client import HTTPException
from io import BytesIO
import re
from threading import BoundedSemaphore
from typing import Literal
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zlib import error as ZlibError


MapDetailSource = Literal["landscape", "satellite", "street"]
_MAX_ZOOM = {"landscape": 14, "satellite": 13, "street": 14}
_WMS_LAYERS = {"landscape": "terrain-light", "satellite": "s2cloudless"}
_WMS_URL = "https://tiles.maps.eox.at/wms"
_STREET_URL = "https://tiles.openfreemap.org/planet/latest"
_MAX_TILE_BYTES = 2 * 1024 * 1024
_CONNECTION_TIMEOUT = 5
_SLOTS = BoundedSemaphore(8)
_RASTER_SIGNATURES = {"image/jpeg": b"\xff\xd8\xff", "image/png": b"\x89PNG\r\n\x1a\n"}
_VECTOR_TYPES = {"application/vnd.mapbox-vector-tile", "application/x-protobuf", "application/protobuf", "application/octet-stream"}
_USER_AGENT = "FaceManager/OnlineMap (+https://github.com/KaiPressmar/face-manager)"


class MapDetailUnavailable(Exception):
    """The map provider did not deliver a valid tile."""


class MapDetailBusy(Exception):
    """The existing tile requests occupy every download slot."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class MapDetailTile:
    data: bytes
    media_type: str
    cache_control: str


def _provider_url(source: MapDetailSource, z: int, x: int, y: int) -> str:
    if source not in _MAX_ZOOM or not 0 <= z <= _MAX_ZOOM[source]:
        raise ValueError("Ungültige Zoomstufe für Kartendetails.")
    rows = 1 << z
    columns = rows if source == "street" else 2 * rows
    if not 0 <= x < columns or not 0 <= y < rows:
        raise ValueError("Ungültige Koordinaten für eine Kartenkachel.")
    if source == "street":
        return f"{_STREET_URL}/{z}/{x}/{y}.pbf"

    degrees = 180 / rows
    west = -180 + x * degrees
    north = 90 - y * degrees
    query = urlencode({
        "SERVICE": "WMS", "VERSION": "1.1.1", "REQUEST": "GetMap", "SRS": "EPSG:4326",
        "LAYERS": _WMS_LAYERS[source], "BBOX": ",".join(format(value, ".15g") for value in (
            west, north - degrees, west + degrees, north,
        )),
        "WIDTH": 256, "HEIGHT": 256, "FORMAT": "image/jpeg", "STYLES": "",
    })
    return f"{_WMS_URL}?{query}"


def _cache_control(value: str) -> str:
    fields = [field.strip().lower() for field in value.split(",")]
    if "no-store" in fields:
        return "private, no-store"
    if "no-cache" in fields:
        return "private, no-cache"
    for field in fields:
        match = re.fullmatch(r"max-age=\"?([0-9]{1,10})\"?", field)
        if match:
            return f"private, max-age={min(86400, int(match.group(1)))}"
    return "private, max-age=300"


def fetch_map_detail_tile(source: MapDetailSource, z: int, x: int, y: int) -> MapDetailTile:
    url = _provider_url(source, z, x, y)
    if not _SLOTS.acquire(blocking=False):
        raise MapDetailBusy
    try:
        media_types = _VECTOR_TYPES if source == "street" else _RASTER_SIGNATURES.keys()
        request = Request(url, headers={
            "User-Agent": _USER_AGENT, "Accept": ", ".join(sorted(media_types)), "Accept-Encoding": "identity",
        })
        with build_opener(_NoRedirect()).open(request, timeout=_CONNECTION_TIMEOUT) as response:
            media_type = response.headers.get_content_type()
            encoding = response.headers.get("Content-Encoding", "").strip().lower()
            if response.status != 200 or media_type not in media_types or encoding not in ("", "identity", "gzip"):
                raise MapDetailUnavailable
            data = response.read(_MAX_TILE_BYTES + 1)
            if len(data) > _MAX_TILE_BYTES:
                raise MapDetailUnavailable
            if encoding == "gzip":
                with GzipFile(fileobj=BytesIO(data)) as decoded:
                    data = decoded.read(_MAX_TILE_BYTES + 1)
            if not data or len(data) > _MAX_TILE_BYTES:
                raise MapDetailUnavailable
            if source != "street" and not data.startswith(_RASTER_SIGNATURES[media_type]):
                raise MapDetailUnavailable
            return MapDetailTile(
                data,
                "application/vnd.mapbox-vector-tile" if source == "street" else media_type,
                _cache_control(", ".join(response.headers.get_all("Cache-Control", ()))),
            )
    except (URLError, OSError, HTTPException, EOFError, ZlibError) as exc:
        raise MapDetailUnavailable from exc
    finally:
        _SLOTS.release()
