"""System Replay Validator — Dual-run inference consistency certification.

Specifies whether the Truth Layer is the canonical source of truth
by comparing Φ_live ≈ Φ_replay at EPSILON = 1e-9.

If this test fails, replay does NOT represent the original universe,
and any conclusion from the inferred system is suspect.

Modules:
    tensor_comparator    — component-by-component Φ comparison
    validation_report    — structured pass/fail report
    system_replay_validator — orchestrator
"""

from .system_replay_validator import SystemReplayValidator
from .tensor_comparator import TensorComparator, TensorDrift
from .validation_report import SrvReport, SrvCategory, SrvStatus

__all__ = [
    "SystemReplayValidator",
    "TensorComparator",
    "TensorDrift",
    "SrvReport",
    "SrvCategory",
    "SrvStatus",
]
