"""Stage Android photobook exports in private storage for the system document picker."""

from __future__ import annotations

import re
import secrets
import time

from fastapi import APIRouter, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool

from backend.config import get_data_root

router = APIRouter(prefix="/api/android/exports")
_FORMATS = {
    "image/png": (b"\x89PNG\r\n\x1a\n", 40 * 1024 * 1024),
    "application/zip": (b"PK\x03\x04", 64 * 1024 * 1024),
}
_TOKEN = re.compile(r"[0-9a-f]{32}")


@router.post("")
async def stage_export(request: Request):
    """Stream a generated PNG or ZIP into a bounded, short-lived staging file."""
    content_type = request.headers.get("content-type", "")
    if content_type not in _FORMATS:
        raise HTTPException(status_code=415, detail="Das Dateiformat wird nicht unterstützt.")
    signature, limit = _FORMATS[content_type]
    folder = get_data_root() / "exports"
    folder.mkdir(parents=True, exist_ok=True)
    live = 0
    for candidate in folder.glob("album-*.upload*"):
        try:
            if candidate.stat().st_mtime < time.time() - 600:
                candidate.unlink(missing_ok=True)
            else:
                live += 1
        except FileNotFoundError:
            continue
    if live >= 4:
        raise HTTPException(status_code=409, detail="Es sind zu viele Dateiexporte offen.")

    token = secrets.token_hex(16)
    staged = folder / f"album-{token}.upload"
    temporary = staged.with_suffix(".upload.part")
    size = 0
    header = bytearray()
    try:
        with temporary.open("xb") as output:
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    raise HTTPException(status_code=413, detail="Die Datei ist zu groß.")
                header.extend(chunk[:len(signature) - len(header)])
                if len(header) == len(signature) and header != signature:
                    raise HTTPException(status_code=422, detail="Die Datei konnte nicht gelesen werden.")
                if chunk:
                    await run_in_threadpool(output.write, chunk)
        if header != signature:
            raise HTTPException(status_code=422, detail="Die Datei konnte nicht gelesen werden.")
        temporary.replace(staged)
        return {"token": token}
    finally:
        temporary.unlink(missing_ok=True)


@router.delete("/{token}", status_code=204)
def discard_export(token: str):
    if not _TOKEN.fullmatch(token):
        raise HTTPException(status_code=404)
    (get_data_root() / "exports" / f"album-{token}.upload").unlink(missing_ok=True)
    return Response(status_code=204)
