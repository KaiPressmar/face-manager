"""HTTP endpoints for managing the folders a library can scan."""

from contextlib import contextmanager
import os
import sqlite3

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from ..services.desktop import normalize_import_folder_path, to_display_path
from ..services.storage import get_person_suggestion_epoch
from . import library


class _LibraryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    library_id: str


class _FolderRequest(_LibraryRequest):
    folder_path: str = Field(min_length=1)
    confirm: bool = False


class _SourceSettings(_LibraryRequest):
    auto_scan: bool | None = None
    auto_import: bool | None = None


class _ScanRequest(_LibraryRequest):
    import_new: bool = False


class _ResolutionRequest(_LibraryRequest):
    original_path: str = Field(min_length=1)
    new_path: str = Field(min_length=1)


def normalized_source_path(path: str) -> str:
    if "\0" in path or not path.strip():
        raise HTTPException(status_code=422, detail="Wähle einen Bilderordner aus.")
    return os.path.abspath(normalize_import_folder_path(path))


@contextmanager
def _edit(library_id: str):
    generation = library.get_generation()
    with library.write_lock:
        library.get_generation(generation)
        if library_id != get_person_suggestion_epoch():
            raise HTTPException(status_code=409, detail="Die Bibliothek wurde gewechselt. Lade die Ordnerliste neu.")
        try:
            yield
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="Dieser Ordner wird bereits verwaltet.") from exc


def create_import_sources_router(sources, display_platform):
    router = APIRouter(prefix="/api/import-sources")

    def display(source: dict, request: Request) -> dict:
        if display_platform(request) == "windows":
            source["folder_path"] = to_display_path(source["folder_path"])
            if source["pending_path"]:
                source["pending_path"] = to_display_path(source["pending_path"])
            for choice in source["conflict_candidates"]:
                choice["original_path"] = to_display_path(choice["original_path"])
                choice["candidate_paths"] = [to_display_path(path) for path in choice["candidate_paths"]]
        return source

    @router.get("")
    def list_sources(request: Request, response: Response):
        generation = library.get_generation()
        identity = get_person_suggestion_epoch()
        entries = sources.list()
        library.get_generation(generation)
        response.headers["Cache-Control"] = "no-store"
        return {"library_id": identity, "sources": [display(entry, request) for entry in entries]}

    @router.post("", status_code=201)
    def register_source(body: _FolderRequest, request: Request):
        with _edit(body.library_id):
            return display(sources.register(normalized_source_path(body.folder_path)), request)

    @router.patch("/{source_id}")
    def update_source(source_id: str, body: _SourceSettings, request: Request):
        with _edit(body.library_id):
            return display(sources.update(source_id, auto_scan=body.auto_scan, auto_import=body.auto_import), request)

    @router.delete("/{source_id}", status_code=204)
    def remove_source(source_id: str, library_id: str):
        with _edit(library_id):
            sources.remove(source_id)
        return Response(status_code=204)

    @router.post("/{source_id}/scan", status_code=202)
    def scan_source(source_id: str, body: _ScanRequest, request: Request):
        with _edit(body.library_id):
            return display(sources.schedule(source_id, import_new=body.import_new), request)

    @router.post("/{source_id}/relink", status_code=202)
    def relink_source(source_id: str, body: _FolderRequest, request: Request):
        with _edit(body.library_id):
            return display(sources.relink(
                source_id, normalized_source_path(body.folder_path), confirm=body.confirm,
            ), request)

    @router.delete("/{source_id}/relink")
    def cancel_source_preview(source_id: str, library_id: str, request: Request):
        with _edit(library_id):
            return display(sources.cancel_preview(source_id), request)

    @router.post("/{source_id}/resolve", status_code=202)
    def resolve_source(source_id: str, body: _ResolutionRequest, request: Request):
        with _edit(body.library_id):
            return display(sources.resolve(
                source_id, normalized_source_path(body.original_path), normalized_source_path(body.new_path),
            ), request)

    return router
