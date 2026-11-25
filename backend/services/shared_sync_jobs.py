"""Scoped analysis jobs and resumable leases over the shared transport.

This module is synchronous and belongs in the filesystem helper process.
Source verification, inference, and policy qualification are caller duties.
"""

from __future__ import annotations

import hashlib
import json
import re

from .shared_sync_transport import (
    HeartbeatObservation,
    InvalidTransportData,
    MAX_ATTEMPTS,
    MAX_EVENTS_PER_BATCH,
    PublishConflictError,
    ShareTransport,
)


_VERSION = 1
_EVENT_TYPE = "analysis-job"
_RESULT_KIND = "analysis"
_ID = re.compile(r"[0-9a-f]{32}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


def _identifier(value: object, pattern: re.Pattern, field: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise InvalidTransportData(f"Invalid analysis job {field}")
    return value


def _json_data(value: object) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise InvalidTransportData("Invalid shared analysis data") from exc


def make_job_descriptor(manifest: dict, photo_sha256: str, pipeline_signature: str) -> dict:
    """Identify one verified photo version and exact inference pipeline."""
    if not isinstance(manifest, dict):
        raise InvalidTransportData("Invalid analysis job scope")
    descriptor = {
        "version": _VERSION,
        "scope": _identifier(manifest.get("scope"), _ID, "scope"),
        "generation": _identifier(manifest.get("generation"), _ID, "generation"),
        "photo_sha256": _identifier(photo_sha256, _SHA256, "photo hash"),
        "pipeline_signature": _identifier(pipeline_signature, _SHA256, "pipeline signature"),
    }
    identity = hashlib.sha256(b"face-manager-shared-analysis\0" + _json_data(descriptor)).hexdigest()
    return {**descriptor, "job_id": identity}


def validate_job_descriptor(manifest: dict, descriptor: dict) -> dict:
    """Verify the descriptor's scope and deterministic job ID."""
    if not isinstance(descriptor, dict) or type(descriptor.get("version")) is not int:
        raise InvalidTransportData("Invalid shared analysis job descriptor")
    expected = make_job_descriptor(
        manifest, descriptor.get("photo_sha256"), descriptor.get("pipeline_signature")
    )
    if descriptor != expected:
        raise InvalidTransportData("Shared analysis job descriptor differs from its identity")
    return expected


def make_job_event(manifest: dict, descriptor: dict) -> dict:
    return {"type": _EVENT_TYPE, "job": validate_job_descriptor(manifest, descriptor)}


def append_job_batch(
    transport: ShareTransport, producer_id: str, batch_no: int,
    first_sequence: int, descriptors: list[dict],
) -> dict:
    """Publish a dedicated job batch at an already reserved producer position."""
    if not isinstance(descriptors, list) or not 1 <= len(descriptors) <= MAX_EVENTS_PER_BATCH:
        raise InvalidTransportData("Invalid shared analysis job batch size")
    manifest = transport.open()
    events = [make_job_event(manifest, descriptor) for descriptor in descriptors]
    return transport.append_events(producer_id, batch_no, first_sequence, events)


def read_job_batch(transport: ShareTransport, producer_id: str, batch_no: int) -> list[dict] | None:
    """Read job descriptors while allowing other domain events in the same stream."""
    manifest = transport.open()
    batch = transport.get_event_batch(producer_id, batch_no)
    if batch is None:
        return None
    jobs = {}
    for event in batch["events"]:
        if event.get("type") != _EVENT_TYPE:
            continue
        if event.keys() != {"type", "job"}:
            raise InvalidTransportData("Invalid shared analysis job event")
        descriptor = validate_job_descriptor(manifest, event["job"])
        jobs[descriptor["job_id"]] = descriptor
    return list(jobs.values())


class SharedJobScheduler:
    """Coordinate one device session without declaring another session's I/O fresh."""

    def __init__(self, transport: ShareTransport, device_id: str, session_id: str):
        self.transport = transport
        self.device_id = _identifier(device_id, _ID, "device")
        self.session_id = _identifier(session_id, _ID, "session")
        self.manifest = transport.open()

    def _job(self, descriptor: dict) -> dict:
        return validate_job_descriptor(self.manifest, descriptor)

    def inspect(self, job: dict) -> dict:
        """Return the first open attempt, winner, or retry exhaustion."""
        job = self._job(job)
        self.transport.open()
        job_id = job["job_id"]
        for attempt in range(1, MAX_ATTEMPTS + 1):
            decision = self.transport.get_decision(job_id, attempt)
            if decision is not None:
                if decision["state"] == "expired":
                    continue
                if decision["result_kind"] != _RESULT_KIND:
                    raise InvalidTransportData("Unexpected shared analysis result kind")
                return {"state": "completed", "attempt": attempt, "decision": decision}
            claim = self.transport.get_claim(job_id, attempt)
            if claim is None:
                return {"state": "pending", "attempt": attempt}
            owned = (claim["device_id"], claim["session_id"]) == (self.device_id, self.session_id)
            return {"state": "owned" if owned else "reserved", "attempt": attempt, "claim": claim}
        return {"state": "exhausted", "attempt": MAX_ATTEMPTS}

    def claim(self, job: dict) -> dict:
        job = self._job(job)
        status = self.inspect(job)
        if status["state"] != "pending":
            return status
        attempt = status["attempt"]
        try:
            claim = self.transport.claim_attempt(job["job_id"], attempt, self.device_id, self.session_id)
        except PublishConflictError:
            # The conflicting slot may already have completed or expired.
            return self.inspect(job)
        return {"state": "owned", "attempt": attempt, "claim": claim}

    def heartbeat(self, job: dict, attempt: int, counter: int) -> None:
        job = self._job(job)
        self.transport.write_heartbeat(job["job_id"], attempt, self.device_id, self.session_id, counter)

    def observe(
        self, job: dict, attempt: int, previous: HeartbeatObservation | None = None,
        *, server_fresh: bool = False,
    ) -> HeartbeatObservation:
        job = self._job(job)
        return self.transport.observe_heartbeat(job["job_id"], attempt, previous, server_fresh=server_fresh)

    def expire(
        self, job: dict, observation: HeartbeatObservation, *,
        server_fresh: bool = False, lease_seconds: float = 60,
    ) -> dict:
        job = self._job(job)
        if not isinstance(observation, HeartbeatObservation):
            raise InvalidTransportData("A fresh local lease observation is required")
        return self.transport.expire_attempt(
            job["job_id"], observation.attempt, observation,
            server_fresh=server_fresh, lease_seconds=lease_seconds,
        )

    def _owned_claim(self, job_id: str, attempt: int) -> dict:
        claim = self.transport.get_claim(job_id, attempt)
        if claim is None or (claim["device_id"], claim["session_id"]) != (self.device_id, self.session_id):
            raise PublishConflictError("This session does not own the analysis attempt")
        return claim

    @staticmethod
    def _result_payload(job: dict, attempt: int, claim: dict, analysis: dict) -> dict:
        if not isinstance(analysis, dict):
            raise InvalidTransportData("Shared analysis results must be JSON objects")
        return {
            "job_id": job["job_id"], "job": job, "attempt": attempt,
            "device_id": claim["device_id"], "session_id": claim["session_id"],
            "analysis": analysis,
        }

    def _read_result(self, job: dict, attempt: int, claim: dict, digest: str) -> dict:
        result = self.transport.get_object(_RESULT_KIND, digest)
        if result is None or type(result.get("attempt")) is not int:
            raise InvalidTransportData("Shared analysis result is missing or invalid")
        validate_job_descriptor(self.manifest, result.get("job"))
        expected = self._result_payload(job, attempt, claim, result.get("analysis"))
        if result != expected:
            raise InvalidTransportData("Shared analysis result differs from the job or owner")
        return result

    def stage_result(self, job: dict, attempt: int, result: dict) -> str:
        """Persist a result object before publishing its terminal decision."""
        job = self._job(job)
        job_id = job["job_id"]
        claim = self._owned_claim(job_id, attempt)
        if self.transport.get_decision(job_id, attempt) is not None:
            raise PublishConflictError("This analysis attempt is already closed")
        return self.transport.put_object(_RESULT_KIND, self._result_payload(job, attempt, claim, result))

    def finish_result(self, job: dict, attempt: int, digest: str) -> dict:
        """Commit a previously staged result, or confirm its identical replay."""
        job = self._job(job)
        job_id = job["job_id"]
        claim = self._owned_claim(job_id, attempt)
        decision = self.transport.get_decision(job_id, attempt)
        if decision is not None and (
            decision["state"] != "completed"
            or decision["result_kind"] != _RESULT_KIND
            or decision["result_digest"] != digest
        ):
            raise PublishConflictError("Another decision closed this analysis attempt")
        self._read_result(job, attempt, claim, digest)
        if decision is not None:
            return decision
        return self.transport.finish_attempt(job_id, attempt, self.device_id, self.session_id, digest)

    def publish_result(self, job: dict, attempt: int, result: dict) -> dict:
        """Commit a result or reconcile a lost acknowledgement with its exact data."""
        job = self._job(job)
        job_id = job["job_id"]
        claim = self._owned_claim(job_id, attempt)
        decision = self.transport.get_decision(job_id, attempt)
        if decision is None:
            try:
                digest = self.stage_result(job, attempt, result)
                return self.finish_result(job, attempt, digest)
            except PublishConflictError:
                decision = self.transport.get_decision(job_id, attempt)
                if decision is None:
                    raise
        if decision["state"] != "completed" or decision["result_kind"] != _RESULT_KIND:
            raise PublishConflictError("Another decision closed this analysis attempt")
        existing = self._read_result(job, attempt, claim, decision["result_digest"])
        if _json_data(existing) != _json_data(self._result_payload(job, attempt, claim, result)):
            raise PublishConflictError("Different analysis data was already accepted")
        return decision

    def get_result(self, job: dict) -> dict | None:
        """Return only a verified, committed analysis payload."""
        job = self._job(job)
        status = self.inspect(job)
        if status["state"] != "completed":
            return None
        attempt = status["attempt"]
        claim = self.transport.get_claim(job["job_id"], attempt)
        if claim is None:
            raise InvalidTransportData("Shared analysis winner has no claim")
        return self._read_result(job, attempt, claim, status["decision"]["result_digest"])["analysis"]
