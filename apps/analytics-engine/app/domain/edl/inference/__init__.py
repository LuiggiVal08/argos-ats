"""Edge Inference Layer — spec Sections 15–19.

Transforms observed trajectory data Φ(D) → Edge Tensor [E_d, E_t, E_e, E_s]ᵀ
under hypothesis-conditioned counterfactual projections.

This layer does not decide whether edge exists.
It decides whether edge is identifiable under admissible counterfactual
uncertainty (spec Section 17.11).
"""

from .edge_tensor import EdgeComponent, EdgeTensor
from .inference_engine import EdgeInferenceEngine
from .nulls import BaseNull, BlockNull, ExecutionNoiseNull, PermutationNull

__version__ = "0.1.0"

__all__ = [
    "EdgeTensor",
    "EdgeComponent",
    "EdgeInferenceEngine",
    "BaseNull",
    "PermutationNull",
    "BlockNull",
    "ExecutionNoiseNull",
]
