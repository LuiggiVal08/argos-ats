from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

_COMPONENT_TYPES = frozenset({"lstm", "xgb", "meta", "calibrator"})


@dataclass(frozen=True)
class ComponentVersion:
    component: str
    major: int
    minor: int
    patch: int
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"))

    def __post_init__(self) -> None:
        if self.component not in _COMPONENT_TYPES:
            raise ValueError(f"component must be one of {_COMPONENT_TYPES}, got {self.component!r}")

    def __str__(self) -> str:
        return f"{self.component}/v{self.major}.{self.minor}.{self.patch}-{self.timestamp}"

    @property
    def prefix(self) -> str:
        return f"{self.component}/v{self.major}.{self.minor}.{self.patch}"

    @property
    def semver_key(self) -> tuple[str, int, int, int]:
        return (self.component, self.major, self.minor, self.patch)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, ComponentVersion):
            return NotImplemented
        return self.semver_key < other.semver_key

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ComponentVersion):
            return NotImplemented
        return self.semver_key == other.semver_key

    def __hash__(self) -> int:
        return hash(self.semver_key)
