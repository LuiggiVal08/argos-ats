"""EdgeInferenceEngine — orquestador principal de la Inference Layer.

Transforma datos de trayectorias observadas en estimaciones del
Edge Tensor Φ(D) = [E_d, E_t, E_e, E_s]ᵀ, con validación EIA.

Spec reference: Section 17.
"""

from __future__ import annotations

from functools import partial
from typing import Any, Callable

import numpy as np

from .edge_tensor import EdgeComponent, EdgeTensor
from .nulls import BaseNull
from .projections import (
    estimate_directional,
    estimate_execution,
    estimate_structural,
    estimate_timing,
)
from .validation.identifiability import eia_all_conditions


class EdgeInferenceEngine:
    """Inference engine that maps D → E(D) under class ℍ₀.

    Usage
    -----
        engine = EdgeInferenceEngine(null_suite=[perm_null, block_null],
                                      utility_fn=my_u_function)
        tensor = engine.infer(model_trades)
        print(tensor.to_dict())
    """

    def __init__(
        self,
        null_suite: list[BaseNull],
        utility_fn: Callable[[Any], float],
        eia_separability_threshold: float = 1e-6,
        eia_null_epsilon: float = 0.1,
        eia_stability_threshold: float = 2.0,
        n_null_samples: int = 500,
        n_bootstrap: int = 200,
        seed: int | None = None,
    ) -> None:
        if not null_suite:
            raise ValueError("null_suite must contain at least one H₀ operator")

        self._null_suite = null_suite
        self._utility_fn = utility_fn
        self._sep_threshold = eia_separability_threshold
        self._null_epsilon = eia_null_epsilon
        self._stab_threshold = eia_stability_threshold
        self._n_null_samples = n_null_samples
        self._n_bootstrap = n_bootstrap
        self._seed = seed

    def _rng(self) -> np.random.Generator:
        return np.random.default_rng(self._seed)

    def infer(self, model_trajectory: Any) -> EdgeTensor:
        """Compute Φ(D) = [E_d, E_t, E_e, E_s]ᵀ.

        Parameters
        ----------
        model_trajectory : Any
            Observed strategy trajectory (list of trade dicts).

        Returns
        -------
        EdgeTensor
            Complete edge measurement with identifiability flags.
        """
        rng = self._rng()

        # Generate null samples from all null operators
        null_samples: dict[str, list[Any]] = {}
        per_null_tensors: dict[str, dict[str, float]] = {}
        n_per_null = max(1, self._n_null_samples // len(self._null_suite))

        for null in self._null_suite:
            samples = null.sample(model_trajectory, n=n_per_null, rng=rng)
            null_samples[null.name()] = samples

            # Compute per-null edge (for EIA Null Invariance)
            per_null_tensors[null.name()] = {
                "E_d": estimate_directional(
                    model_trajectory, samples, self._utility_fn, rng
                )["mean"],
                "E_t": estimate_timing(
                    model_trajectory, samples, rng
                )["mean"],
                "E_e": estimate_execution(
                    model_trajectory, samples, rng
                )["mean"],
                "E_s": estimate_structural(
                    model_trajectory, samples, rng
                )["mean"],
            }

        # Aggregate all null samples into one pool
        all_null_samples: list[Any] = []
        for samples in null_samples.values():
            all_null_samples.extend(samples)

        # ── Compute each component ─────────────────────────────────────
        rng_bootstrap = np.random.default_rng(self._seed + 1)

        def _bootstrap_component(
            estimator_fn: Callable,
            *,
            fixed_kwargs: dict | None = None,
        ) -> tuple[dict[str, float], list[float]]:
            kw = dict(fixed_kwargs or {})
            base = estimator_fn(model_trajectory, all_null_samples, **kw, rng=rng)
            bootstraps = []
            for _ in range(self._n_bootstrap):
                try:
                    boot = estimator_fn(
                        model_trajectory,
                        list(rng_bootstrap.choice(
                            all_null_samples,
                            size=len(all_null_samples),
                            replace=True,
                        )),
                        **kw,
                        rng=rng_bootstrap,
                    )
                    bootstraps.append(boot["mean"])
                except Exception:
                    bootstraps.append(base["mean"])
            return base, bootstraps

        # E_d
        ed_base, ed_boot = _bootstrap_component(
            estimate_directional,
            fixed_kwargs={"utility_fn": self._utility_fn},
        )
        ed_id, _ = eia_all_conditions(
            ed_base["mean"],
            {k: v["E_d"] for k, v in per_null_tensors.items()},
            ed_boot,
            self._sep_threshold,
            self._null_epsilon,
            self._stab_threshold,
        )
        e_d = EdgeComponent(
            mean=ed_base["mean"],
            std=ed_base["std"],
            ci_lower=ed_base["mean"] - 1.96 * ed_base["std"],
            ci_upper=ed_base["mean"] + 1.96 * ed_base["std"],
            identifiable=ed_id,
        )

        # E_t
        et_base, et_boot = _bootstrap_component(estimate_timing)
        et_id, _ = eia_all_conditions(
            et_base["mean"],
            {k: v["E_t"] for k, v in per_null_tensors.items()},
            et_boot,
            self._sep_threshold,
            self._null_epsilon,
            self._stab_threshold,
        )
        e_t = EdgeComponent(
            mean=et_base["mean"],
            std=et_base["std"],
            ci_lower=et_base["mean"] - 1.96 * et_base["std"],
            ci_upper=et_base["mean"] + 1.96 * et_base["std"],
            identifiable=et_id,
        )

        # E_e
        ee_base, ee_boot = _bootstrap_component(estimate_execution)
        ee_id, _ = eia_all_conditions(
            ee_base["mean"],
            {k: v["E_e"] for k, v in per_null_tensors.items()},
            ee_boot,
            self._sep_threshold,
            self._null_epsilon,
            self._stab_threshold,
        )
        e_e = EdgeComponent(
            mean=ee_base["mean"],
            std=ee_base["std"],
            ci_lower=ee_base["mean"] - 1.96 * ee_base["std"],
            ci_upper=ee_base["mean"] + 1.96 * ee_base["std"],
            identifiable=ee_id,
        )

        # E_s
        es_base, es_boot = _bootstrap_component(estimate_structural)
        es_id, _ = eia_all_conditions(
            es_base["mean"],
            {k: v["E_s"] for k, v in per_null_tensors.items()},
            es_boot,
            self._sep_threshold,
            self._null_epsilon,
            self._stab_threshold,
        )
        e_s = EdgeComponent(
            mean=es_base["mean"],
            std=es_base["std"],
            ci_lower=es_base["mean"] - 1.96 * es_base["std"],
            ci_upper=es_base["mean"] + 1.96 * es_base["std"],
            identifiable=es_id,
        )

        return EdgeTensor(
            directional=e_d,
            timing=e_t,
            execution=e_e,
            structural=e_s,
        )
