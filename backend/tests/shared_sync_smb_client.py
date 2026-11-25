"""Run one side of the Windows UNC and Ubuntu CIFS interoperability proof."""

import argparse
import json
import platform
import time
import uuid
from pathlib import Path
from unittest.mock import patch

from backend.services import shared_sync_transport
from backend.services.shared_sync_transport import PublishConflictError, ShareTransport


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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("share", type=Path)
    parser.add_argument("run_id", type=lambda value: uuid.UUID(value).hex)
    parser.add_argument("client", choices=("windows", "ubuntu"))
    args = parser.parse_args()
    run_client(args.share, args.run_id, args.client)
