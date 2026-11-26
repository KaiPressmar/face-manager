"""Verified image bytes and coordinate proof for manual face marking."""

import hashlib
import hmac
import json
import secrets
from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass
from io import BytesIO
from time import monotonic, time

from PIL import Image

from .preview_cache import (
    FACE_PREVIEW_MAX_SOURCE_BYTES,
    bounded_upright_face_preview,
    snapshot_preview_source,
)

PREVIEW_PROOF_HEADER = "X-Face-Preview"
PREVIEW_ORIGINAL_HEADER = "X-Face-Preview-Original"
_PROOF_KEY = secrets.token_bytes(32)
_PROOF_TTL = 2 * 60 * 60
_SOURCE_DEADLINE = 7


@dataclass(frozen=True)
class FacePreview:
    body: bytes
    geometry: tuple[int, int, int]
    source_hash: str


def render_face_preview(paths, content_hash: str | None) -> FacePreview | None:
    """Render original bytes whose digest matches the registered photo, if any."""
    deadline = monotonic() + _SOURCE_DEADLINE
    for path in paths:
        if monotonic() >= deadline:
            return None
        try:
            with snapshot_preview_source(
                path, maximum_bytes=FACE_PREVIEW_MAX_SOURCE_BYTES, require_stable_path=not content_hash,
            ) as (source, digest):
                if content_hash and digest != content_hash:
                    continue
                with bounded_upright_face_preview(source) as (upright, geometry), BytesIO() as buffer:
                    upright.save(buffer, format="JPEG", quality=89)
                    return FacePreview(buffer.getvalue(), geometry, digest)
        except (OSError, ValueError, Image.DecompressionBombError):
            continue
    return None


def find_preview_source(paths, source_hash: str) -> str | None:
    """Find a registered legacy path still supplying the bytes shown to the user."""
    deadline = monotonic() + _SOURCE_DEADLINE
    for path in paths:
        if monotonic() >= deadline:
            return None
        try:
            with snapshot_preview_source(
                path, maximum_bytes=FACE_PREVIEW_MAX_SOURCE_BYTES, require_stable_path=True,
            ) as (_, digest):
                if digest == source_hash:
                    return path
        except OSError:
            continue
    return None


def issue_preview_proof(image_id: int, generation: int, content_hash: str | None, preview: FacePreview) -> str:
    expires_at = int(time()) + _PROOF_TTL
    payload = json.dumps(
        [image_id, generation, content_hash, preview.geometry, preview.source_hash, expires_at],
        separators=(",", ":"),
    ).encode("utf-8")
    encoded = urlsafe_b64encode(payload).decode("ascii")
    signature = hmac.new(_PROOF_KEY, encoded.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def read_preview_proof(token, image_id: int, generation: int, content_hash: str | None):
    """Return geometry and the displayed byte hash if the proof matches this photo."""
    if not isinstance(token, str) or len(token) > 1024 or not token.isascii():
        return None
    encoded, _, signature = token.partition(".")
    expected = hmac.new(_PROOF_KEY, encoded.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return None
    # Only payloads issued by this process reach the parser.
    preview_image_id, preview_generation, preview_content_hash, geometry, source_hash, expires_at = json.loads(
        urlsafe_b64decode(encoded),
    )
    if (preview_image_id, preview_generation, preview_content_hash) != (image_id, generation, content_hash):
        return None
    if expires_at < time():
        return None
    return tuple(geometry), source_hash
