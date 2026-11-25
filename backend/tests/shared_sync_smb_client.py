"""Run one side of the Windows UNC and Ubuntu CIFS interoperability proof."""

import argparse
import base64
import hashlib
import json
import os
import platform
import struct
import time
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.services import shared_sync_transport
from backend.services.shared_sync_transport import PublishConflictError, ShareTransport


_PROOF_PHOTOS = {
    # Distinct 1×1 PNGs let each platform publish a new photo version.
    "windows": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC",
    "ubuntu": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgaGAAAAEEAIFw9selAAAAAElFTkSuQmCC",
}


def _prove_nested_scopes(root, client, meet):
    from backend.services.shared_sync_worker import run_request

    ordinary = root / "ordinary album"
    separate = root / "independent album"
    if client == "windows":
        ordinary.mkdir()
    else:
        (separate / ".fm").mkdir(parents=True)
    meet("nested-scopes-ready")

    for child, expected in ((ordinary, "ordinary_child"), (separate, "separate")):
        result = run_request({"action": "inspect_nested_scope", "parent": str(root), "child": str(child)})
        if result.get("status") != expected:
            raise RuntimeError(f"{client} expected {expected} for {child.name}, got {result}")
    print(f"{client} verified an ordinary child and a separate .fm scope through its SMB path")


def _report_source_verification_failure(source_root, relative_path, verification, expected_sha256):
    from backend.services import shared_sync_worker as worker
    from backend.services.filesystem_paths import filesystem_path

    def identity(info):
        return {
            field: getattr(info, field, None)
            for field in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_birthtime_ns")
        }

    details = {
        "worker_status": verification.get("status"),
        "worker_sha256": verification.get("photo_sha256"),
        "expected_sha256": expected_sha256,
    }
    try:
        parts = worker.portable_relative_path(relative_path)
        named_path = filesystem_path(source_root.joinpath(*parts))
        with worker._open_source(str(source_root), parts) as (opened, initial, stat_entry):
            details["opened_before"] = identity(initial)
            details["worker_entry_before"] = identity(stat_entry())
            details["named_entry_before"] = identity(os.lstat(named_path))
            details["opened_vs_named_differences"] = [
                field for field, value in details["opened_before"].items()
                if value != details["named_entry_before"][field]
            ]
            details["opened_sha256"] = hashlib.file_digest(opened, "sha256").hexdigest()
            details["opened_after"] = identity(os.fstat(opened.fileno()))
            details["worker_entry_after"] = identity(stat_entry())
            details["named_entry_after"] = identity(os.lstat(named_path))
    except (OSError, ValueError, shared_sync_transport.TransportError) as error:
        details["diagnostic_error"] = type(error).__name__
        details["errno"] = getattr(error, "errno", None)
        details["winerror"] = getattr(error, "winerror", None)
    print(f"Source verification diagnostics: {json.dumps(details, sort_keys=True)}", flush=True)


def _prove_announced_work(root, manifest, client, meet):
    from backend.services.shared_sync_jobs import make_job_descriptor
    from backend.services.shared_sync_worker import run_request

    device, session = uuid.uuid4().hex, uuid.uuid4().hex
    source_root = root / "photos"
    rendezvous = root / "rendezvous"
    signature = hashlib.sha256(b"Dependency-free SMB proof model").hexdigest()
    seen = set()

    def request(action, operation, **values):
        reply = run_request({
            "action": action, "operation": operation, "sync_root": str(root / "transport"),
            "known_manifest": manifest, "allow_writes": True,
            "device_id": device, "session_id": session, **values,
        })
        if reply.get("status") != action or reply.get("operation") != operation:
            raise RuntimeError(f"{client} {action} {operation} failed: {reply}")
        return reply["value"]

    for publisher, photo in _PROOF_PHOTOS.items():
        publishing = client == publisher
        relative_path = f"{publisher} album/Übung.png"
        photo_bytes = base64.b64decode(photo)
        if publishing:
            photo_path = source_root / relative_path
            photo_path.parent.mkdir(parents=True)
            photo_path.write_bytes(photo_bytes)
            expected_sha256 = hashlib.sha256(photo_bytes).hexdigest()
            verified = run_request({
                "action": "verify_source", "source_root": str(source_root), "relative_path": relative_path,
                "expected_hash": expected_sha256,
            })
            if verified.get("status") != "verified" or verified.get("photo_sha256") != expected_sha256:
                _report_source_verification_failure(source_root, relative_path, verified, expected_sha256)
                raise RuntimeError(f"{publisher} could not verify its announced photo")
            descriptor = make_job_descriptor(manifest, verified["photo_sha256"], signature)
            announced = request("job_queue", "publish", job=descriptor, relative_path=relative_path)
        meet(f"{publisher}-announced")

        # The assistant has no import request. The worker's queue supplies the
        # new job and the only photo path it will open.
        page = request("job_queue", "list", limit=4)
        notices = [notice for notice in page["announcements"] if notice["announcement_id"] not in seen]
        if not page["at_end"] or len(notices) != 1:
            raise RuntimeError(f"{client} did not discover exactly one new {publisher} announcement")
        notice, = notices
        seen.add(notice["announcement_id"])
        if notice["relative_path"] != relative_path or (publishing and notice != announced):
            raise RuntimeError(f"{client} read a different announcement")
        job = notice["job"]
        if job["pipeline_signature"] != signature:
            raise RuntimeError(f"{client} found an incompatible announced pipeline")

        if not publishing:
            claim = request("job", "claim", job=job)
            if claim["state"] != "owned" or claim["attempt"] != 1:
                raise RuntimeError(f"{client} could not claim the announced job")
        meet(f"{publisher}-claimed")
        if publishing and request("job", "claim", job=job)["state"] != "reserved":
            raise RuntimeError(f"{publisher} could also claim the assistant's job")
        meet(f"{publisher}-reservation-verified")

        expected = {
            "version": 1, "embedding_format": "f32le", "photo_sha256": job["photo_sha256"],
            "pipeline_signature": signature, "geometry": [1, 1, 1], "faces": [],
        }
        if not publishing:
            with TemporaryDirectory(prefix="face-manager-smb-analysis-") as staging:
                staging_root = Path(staging).absolute()
                staged = staging_root / "photo"
                verified = run_request({
                    "action": "stage_source", "source_root": str(source_root),
                    "relative_path": notice["relative_path"], "expected_hash": job["photo_sha256"],
                    "output_path": str(staged),
                }, staging_root=staging_root)
                if verified.get("status") != "verified":
                    raise RuntimeError(f"{client} could not stage the announced photo")
                staged_bytes = staged.read_bytes()
                if staged_bytes != photo_bytes:
                    raise RuntimeError(f"{client} staged different photo bytes")
                # The stand-in model reads the fixture's PNG dimensions and returns no faces.
                geometry = [1, *struct.unpack_from(">II", staged_bytes, 16)]
                (rendezvous / f"{publisher}-computed-{client}").touch(exist_ok=False)
                digest = request("job", "stage_result", job=job, attempt=1, result={**expected, "geometry": geometry})
                request("job", "finish_result", job=job, attempt=1, result_digest=digest)
        meet(f"{publisher}-completed")
        repeated = request("job", "claim", job=job)
        if repeated["state"] != "completed" or request("job", "get_result", job=job) != expected:
            raise RuntimeError(f"{client} did not reuse the assistant's verified analysis result")
        assistant = "ubuntu" if publisher == "windows" else "windows"
        if (rendezvous / f"{publisher}-computed-{publisher}").exists() or not (
            rendezvous / f"{publisher}-computed-{assistant}"
        ).is_file():
            raise RuntimeError(f"{client} did not observe exactly one computation by the assistant")
        print(f"{client} verified {publisher} announcement discovery, one computation, and result reuse")


def run_client(root: Path, run_id: str, client: str) -> None:
    peer = "ubuntu" if client == "windows" else "windows"
    expected_os = "Windows" if client == "windows" else "Linux"
    if platform.system() != expected_os:
        raise RuntimeError(f"{client} client needs {expected_os}, found {platform.system()}")
    transport = ShareTransport(root / "transport", allow_writes=True)
    rendezvous = root / "rendezvous"
    rename = shared_sync_transport._rename_no_replace

    def meet(phase: str) -> None:
        (rendezvous / f"{phase}-{client}").touch(exist_ok=False)
        deadline = time.monotonic() + 25
        marker = rendezvous / f"{phase}-{peer}"
        while not marker.is_file():
            if time.monotonic() >= deadline:
                raise TimeoutError(f"{client} timed out waiting for {peer} at {phase}")
            time.sleep(0.025)

    def race(slot, operation):
        def synchronized_publication(directory, source, destination):
            if destination == slot:
                meet(slot)
            return rename(directory, source, destination)

        with patch.object(shared_sync_transport, "_rename_no_replace", synchronized_publication):
            return operation()

    if not transport.probe_writes():
        raise RuntimeError(f"{client} share failed the write capability probe")
    manifest = race("manifest.json", lambda: transport.open(create=True))

    device, session = uuid.uuid4().hex, uuid.uuid4().hex
    try:
        claim = race("claim.json", lambda: transport.claim_attempt(run_id, 1, device, session))
    except PublishConflictError:
        claim = None

    batch = race(
        "00000000000000000001.json",
        lambda: transport.append_events(run_id, 1, 1, [{"operation": "identical retry"}]),
    )
    report = {"manifest": manifest, "claim": claim, "batch": batch}
    (rendezvous / f"{client}.json").write_text(json.dumps(report), encoding="utf-8")
    meet("verified")
    peer_report = json.loads((rendezvous / f"{peer}.json").read_text(encoding="utf-8"))

    if manifest != peer_report["manifest"] or manifest != transport.open():
        raise RuntimeError("The two clients observed different share manifests")
    claims = [value for value in (claim, peer_report["claim"]) if value is not None]
    if len(claims) != 1 or claims[0] != transport.get_claim(run_id, 1):
        raise RuntimeError("The two clients did not observe one durable claim winner")
    if batch != peer_report["batch"] or batch != transport.get_event_batch(run_id, 1):
        raise RuntimeError("The two clients observed different retry batches")
    print(f"{client} verified one manifest, one winning claim, and one retry batch")
    _prove_nested_scopes(root, client, meet)
    _prove_announced_work(root, manifest, client, meet)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("share", type=Path)
    parser.add_argument("run_id", type=lambda value: uuid.UUID(value).hex)
    parser.add_argument("client", choices=("windows", "ubuntu"))
    args = parser.parse_args()
    run_client(args.share, args.run_id, args.client)
