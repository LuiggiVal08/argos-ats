"""Projection Hasher — deterministic hash of (state, trace, structural, deployment).

EMC v1 §10.4: The projection hash is:

    projection_hash = SHA-256(
        canonical_json(state)
        + '|TRACE|'
        + canonical_json(trace)
        + '|STRUCTURAL|'
        + canonical_json(structural_fingerprint)
        + '|DEPLOYMENT|'
        + canonical_json(deployment_fingerprint)
    )

Property: deterministic given the same (state, trace, structural, deployment).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from types import MappingProxyType
from typing import Any

from .execution_trace import TraceEntry
from .projection_metadata import DeploymentFingerprint, StructuralFingerprint


# ── Fields to exclude from serialization ──────────────────────────
# These are clock-based or runtime metadata, not logical state.

_EXCLUDED_FIELDS: set[str] = {
    "generated_at",     # SystemState timestamp
    "processed_at",     # processing timestamp (if present)
    "session_id",       # runtime session identifier
}


def _clean(obj: Any) -> Any:
    """Recursively clean an object for canonical serialization.

    - Converts dataclasses to dicts (sorted keys).
    - Excludes _EXCLUDED_FIELDS.
    - Sorts dict keys.
    - Preserves list order (deterministic by construction).
    """
    if isinstance(obj, (dict, MappingProxyType)):
        return {
            k: _clean(v)
            for k, v in sorted(obj.items())
            if k not in _EXCLUDED_FIELDS
        }
    if isinstance(obj, (list, tuple)):
        return [_clean(item) for item in obj]
    if is_dataclass(obj) and not isinstance(obj, type):
        # Use to_dict() if available (handles MappingProxyType natively)
        if hasattr(obj, "to_dict"):
            return _clean(obj.to_dict())
        # Manual field iteration (avoids asdict/deepcopy problems)
        fields = {
            f.name: getattr(obj, f.name)
            for f in obj.__dataclass_fields__.values()
        }
        return _clean(fields)
    if isinstance(obj, float):
        # Use repr to avoid precision ambiguity between JSON serializers
        return repr(obj)
    return obj


def canonical_json(obj: Any) -> str:
    """Serialize an object to canonical JSON.

    Rules (EMC v1 §10.7):
      1. Sort dict keys lexicographically.
      2. Serialize floats with repr() for determinism.
      3. Exclude clock-based fields.
      4. Preserve array order (deterministic by construction).
      5. Dataclass fields sorted by key.

    Returns a UTF-8 encoded JSON string without trailing newline.
    """
    cleaned = _clean(obj)
    return json.dumps(cleaned, sort_keys=True, ensure_ascii=False, default=str)


def compute_projection_hash(
    state: Any,
    trace: list[TraceEntry],
    structural_fingerprint: StructuralFingerprint,
    deployment_fingerprint: DeploymentFingerprint,
) -> str:
    """Compute the F3 projection hash.

    Args:
        state: OutputState or any serializable state (has .to_dict()).
        trace: Complete execution trace (list of TraceEntry).
        structural_fingerprint: Execution semantics fingerprint.
        deployment_fingerprint: Version metadata fingerprint.

    Returns:
        Hex digest (64 chars) of SHA-256(
            state + trace + structural + deployment
        ).
    """
    state_json = canonical_json(state)
    trace_json = canonical_json(trace)
    struct_fp_json = canonical_json(structural_fingerprint)
    deploy_fp_json = canonical_json(deployment_fingerprint)
    payload = (
        f"{state_json}|TRACE|{trace_json}"
        f"|STRUCTURAL|{struct_fp_json}"
        f"|DEPLOYMENT|{deploy_fp_json}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "canonical_json",
    "compute_projection_hash",
]
