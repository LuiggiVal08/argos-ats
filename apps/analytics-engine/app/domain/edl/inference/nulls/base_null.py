"""BaseNull interface — contract for all H₀ trajectory transformers.

Every null is a transformation operator Tₖ : τ → τ̃ that preserves
structural invariants (CPA, spec Section 18.2) while breaking
signal alignment.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseNull(ABC):
    """All null operators must implement this interface.

    Each null transforms an observed trajectory into a counterfactual
    one under structure-preserving constraints.
    """

    @abstractmethod
    def transform(self, trajectory: Any, rng: Any = None) -> Any:
        """Transform a trajectory τ → τ̃.

        Parameters
        ----------
        trajectory : Any
            Observed trajectory object (list of TradeEpisode or similar).
        rng : Any, optional
            Random generator for reproducibility.

        Returns
        -------
        Any
            Counterfactual trajectory with same structure but broken
            signal alignment.
        """
        ...

    @abstractmethod
    def name(self) -> str:
        """Human-readable identifier for this null operator."""
        ...

    def sample(self, trajectory: Any, n: int = 1, rng: Any = None) -> list[Any]:
        """Generate n counterfactual trajectories under this null.

        Default implementation calls transform() n times.
        """
        return [self.transform(trajectory, rng=rng) for _ in range(n)]
