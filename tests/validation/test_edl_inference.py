"""Phase 3: EDL Inference Validation.

Tests EdgeTensor, null operators, projections, EIA identifiability,
and EdgeInferenceEngine integration.

Risk level: CRITICAL — untested Bayesian evidence layer.
Covers spec Sections 15–19.

Uses bounded tolerance ε for non-deterministic (bootstrap) outputs.
Determinism checks: fixed seed → identical tensor.
"""

import math
from typing import Any

import numpy as np
import pytest

from app.domain.edl.inference.edge_tensor import EdgeComponent, EdgeTensor
from app.domain.edl.inference.nulls.permutation_null import PermutationNull
from app.domain.edl.inference.nulls.block_null import BlockNull, compute_regimes_roc20
from app.domain.edl.inference.nulls.execution_noise_null import ExecutionNoiseNull
from app.domain.edl.inference.projections.directional import estimate_directional
from app.domain.edl.inference.projections.timing import estimate_timing
from app.domain.edl.inference.projections.execution import estimate_execution
from app.domain.edl.inference.projections.structural import estimate_structural
from app.domain.edl.inference.validation.identifiability import (
    check_separability,
    check_null_invariance,
    check_representation_stability,
    eia_all_conditions,
)
from app.domain.edl.inference.inference_engine import EdgeInferenceEngine


# ── Helpers ──────────────────────────────────────────────────────────

TRADES_10 = [
    {"side": 1, "gross_pnl": 100.0, "costs": 10.0, "net_pnl": 90.0,
     "bar_index": i, "regime": "TRENDING_BULL"}
    for i in range(10)
]
TRADES_10_MIXED = [
    {"side": 1 if i % 2 == 0 else -1,
     "gross_pnl": 100.0 if i % 2 == 0 else -50.0,
     "costs": 10.0, "net_pnl": 90.0 if i % 2 == 0 else -60.0,
     "bar_index": i, "regime": "TRENDING_BULL" if i < 5 else "RANGING"}
    for i in range(10)
]


def _identity_utility(trajectory: Any) -> float:
    return sum(t.get("net_pnl", 0.0) for t in trajectory)


# ═════════════════════════════════════════════════════════════════════
# 1. Edge Tensor Core
# ═════════════════════════════════════════════════════════════════════

class TestEdgeComponent:
    def test_defaults(self):
        c = EdgeComponent()
        assert c.mean == 0.0
        assert c.std == 0.0
        assert c.ci_lower == 0.0
        assert c.ci_upper == 0.0
        assert c.identifiable is False
        assert bool(c) is False

    def test_identifiable_nonzero_is_truthy(self):
        c = EdgeComponent(mean=3.0, std=1.0, identifiable=True)
        assert bool(c) is True

    def test_not_identifiable_is_falsy(self):
        c = EdgeComponent(mean=3.0, std=1.0, identifiable=False)
        assert bool(c) is False

    def test_low_signal_to_noise_is_falsy(self):
        c = EdgeComponent(mean=1.0, std=1.0, identifiable=True)
        assert bool(c) is False

    def test_signal_to_noise_inf_at_zero_std(self):
        c = EdgeComponent(mean=5.0, std=0.0)
        assert c.signal_to_noise == float("inf")

    def test_signal_to_noise(self):
        c = EdgeComponent(mean=10.0, std=2.0)
        assert c.signal_to_noise == 5.0


class TestEdgeTensor:
    def test_defaults(self):
        t = EdgeTensor()
        assert t.directional.mean == 0.0
        assert t.timing.mean == 0.0
        assert t.execution.mean == 0.0
        assert t.structural.mean == 0.0
        assert t.has_any_edge is False
        assert t.has_full_edge is False

    def test_has_any_edge_single_component(self):
        t = EdgeTensor(
            directional=EdgeComponent(mean=3.0, std=1.0, identifiable=True)
        )
        assert t.has_any_edge is True
        assert t.has_full_edge is False

    def test_has_full_edge_all(self):
        kwargs = {"mean": 3.0, "std": 1.0, "identifiable": True}
        t = EdgeTensor(
            directional=EdgeComponent(**kwargs),
            timing=EdgeComponent(**kwargs),
            execution=EdgeComponent(**kwargs),
            structural=EdgeComponent(**kwargs),
        )
        assert t.has_any_edge is True
        assert t.has_full_edge is True

    def test_identifiability_map(self):
        t = EdgeTensor(
            directional=EdgeComponent(identifiable=True),
            timing=EdgeComponent(identifiable=False),
            execution=EdgeComponent(identifiable=False),
            structural=EdgeComponent(identifiable=True),
        )
        id_map = t.identifiability()
        assert id_map["directional"] is True
        assert id_map["timing"] is False
        assert id_map["structural"] is True

    def test_norm(self):
        t = EdgeTensor(
            directional=EdgeComponent(mean=0.5),
            timing=EdgeComponent(mean=0.3),
        )
        n = t.norm()
        assert n["directional"] == 0.5
        assert n["timing"] == 0.3
        assert n["execution"] == 0.0
        assert n["structural"] == 0.0

    def test_to_dict_structure(self):
        t = EdgeTensor(
            directional=EdgeComponent(mean=0.65, std=0.1, ci_lower=0.45, ci_upper=0.85, identifiable=True)
        )
        d = t.to_dict()
        assert "directional" in d
        assert "identifiability" in d
        assert d["directional"]["mean"] == 0.65
        assert d["directional"]["ci"] == [0.45, 0.85]
        assert d["identifiability"]["directional"] is True

    def test_components_returns_all_four(self):
        t = EdgeTensor()
        comps = t.components()
        assert set(comps.keys()) == {"directional", "timing", "execution", "structural"}


# ═════════════════════════════════════════════════════════════════════
# 2. Null Operators (H₀₁, H₀₂, H₀₃)
# ═════════════════════════════════════════════════════════════════════

class TestPermutationNull:
    def test_preserves_trade_count(self):
        null = PermutationNull(seed=42)
        result = null.transform(TRADES_10, rng=np.random.default_rng(42))
        assert len(result) == 10

    def test_preserves_bar_index_order(self):
        null = PermutationNull(seed=42)
        result = null.transform(TRADES_10, rng=np.random.default_rng(42))
        orig_indices = [t["bar_index"] for t in TRADES_10]
        result_indices = [t["bar_index"] for t in result]
        assert result_indices == orig_indices

    def test_breaks_directional_signal(self):
        null = PermutationNull(seed=99)
        rng = np.random.default_rng(99)
        result = null.transform(TRADES_10_MIXED, rng=rng)
        orig_sides = [t["side"] for t in TRADES_10_MIXED]
        result_sides = [int(t["side"]) for t in result]
        assert orig_sides != result_sides

    def test_empty_trajectory(self):
        null = PermutationNull()
        assert null.transform([]) == []

    def test_deterministic_with_seed(self):
        null = PermutationNull(seed=42)
        rng1 = np.random.default_rng(42)
        rng2 = np.random.default_rng(42)
        r1 = null.transform(TRADES_10, rng=rng1)
        r2 = null.transform(TRADES_10, rng=rng2)
        sides1 = [t["side"] for t in r1]
        sides2 = [t["side"] for t in r2]
        assert sides1 == sides2

    def test_sample_generates_n_trajectories(self):
        null = PermutationNull(seed=42)
        samples = null.sample(TRADES_10, n=5, rng=np.random.default_rng(42))
        assert len(samples) == 5
        for s in samples:
            assert len(s) == 10

    def test_preserves_costs(self):
        null = PermutationNull(seed=42)
        result = null.transform(TRADES_10, rng=np.random.default_rng(42))
        orig_costs = [t["costs"] for t in TRADES_10]
        result_costs = [t["costs"] for t in result]
        assert orig_costs == result_costs


class TestBlockNull:
    def test_fixed_size_blocks(self):
        null = BlockNull(block_size=3, seed=42)
        result = null.transform(TRADES_10, rng=np.random.default_rng(42))
        assert len(result) == 10
        assert sorted(result, key=lambda x: x["bar_index"]) == sorted(TRADES_10, key=lambda x: x["bar_index"])

    def test_single_block_no_permutation(self):
        null = BlockNull(block_size=100)
        result = null.transform(TRADES_10)
        assert len(result) == 10

    def test_regime_blocks(self):
        regimes = [0, 0, 0, 0, 0, 1, 1, 1, 1, 1]
        null = BlockNull(block_size=5, regimes=regimes, seed=42)
        result = null.transform(TRADES_10, rng=np.random.default_rng(42))
        assert len(result) == 10

    def test_regime_roc20_computation(self):
        trajectory = [
            {"net_pnl": 10.0, "side": 1},
            {"net_pnl": 15.0, "side": 1},
            {"net_pnl": -5.0, "side": -1},
        ] * 10
        regimes = compute_regimes_roc20(trajectory)
        assert len(regimes) == 30
        assert all(r in (0, 1, 2) for r in regimes)

    def test_empty_trajectory_returns_empty(self):
        null = BlockNull(block_size=5)
        assert null.transform([]) == []

    def test_min_block_size_merges_small_blocks(self):
        regimes = [0, 0, 1, 0, 0, 0, 0, 0, 0, 0]
        null = BlockNull(block_size=5, regimes=regimes, min_block_size=3, seed=42)
        result = null.transform(TRADES_10, rng=np.random.default_rng(42))
        assert len(result) == 10


class TestExecutionNoiseNull:
    def test_preserves_trade_count(self):
        null = ExecutionNoiseNull(seed=42)
        result = null.transform(TRADES_10, rng=np.random.default_rng(42))
        assert len(result) == 10

    def test_preserves_net_pnl_expectation(self):
        null = ExecutionNoiseNull(slippage_std=0.01, cost_noise_std=0.1, seed=42)
        rng = np.random.default_rng(42)
        result = null.transform(TRADES_10, rng=rng)
        orig_total = sum(t["net_pnl"] for t in TRADES_10)
        result_total = sum(t["net_pnl"] for t in result)
        assert abs(result_total - orig_total) < 0.5

    def test_zero_noise_returns_identical(self):
        null = ExecutionNoiseNull(slippage_std=0.0, cost_noise_std=0.0)
        result = null.transform(TRADES_10)
        for orig, res in zip(TRADES_10, result):
            assert orig["net_pnl"] == res["net_pnl"]

    def test_invalid_std_raises(self):
        with pytest.raises(ValueError):
            ExecutionNoiseNull(slippage_std=-1.0)
        with pytest.raises(ValueError):
            ExecutionNoiseNull(cost_noise_std=-0.5)

    def test_empty_trajectory(self):
        null = ExecutionNoiseNull()
        assert null.transform([]) == []

    def test_deterministic_with_seed(self):
        null = ExecutionNoiseNull(seed=42)
        rng1 = np.random.default_rng(42)
        rng2 = np.random.default_rng(42)
        r1 = null.transform(TRADES_10, rng=rng1)
        r2 = null.transform(TRADES_10, rng=rng2)
        assert r1 == r2

    def test_costs_never_negative(self):
        null = ExecutionNoiseNull(slippage_std=0.0, cost_noise_std=2.0, seed=42)
        result = null.transform(TRADES_10, rng=np.random.default_rng(42))
        for t in result:
            assert t["costs"] >= 0.0


# ═════════════════════════════════════════════════════════════════════
# 3. Projections (E_d, E_t, E_e, E_s)
# ═════════════════════════════════════════════════════════════════════

class TestEstimateDirectional:
    def test_identical_trajectories_give_05(self):
        rng = np.random.default_rng(42)
        null = PermutationNull(seed=42)
        null_samples = null.sample(TRADES_10, n=50, rng=rng)
        result = estimate_directional(TRADES_10, null_samples, _identity_utility, rng)
        assert 0.0 <= result["mean"] <= 1.0
        assert result["n_total"] == 50
        assert result["n_wins"] <= 50

    def test_empty_null_samples(self):
        result = estimate_directional(TRADES_10, [], _identity_utility)
        assert result["mean"] == 0.0

    def test_std_zero_for_single_sample(self):
        rng = np.random.default_rng(42)
        null = PermutationNull(seed=42)
        samples = null.sample(TRADES_10, n=1, rng=rng)
        result = estimate_directional(TRADES_10, samples, _identity_utility, rng)
        n_wins = result["n_wins"]
        assert n_wins in (0, 1)


class TestEstimateTiming:
    def test_returns_expected_keys(self):
        rng = np.random.default_rng(42)
        null = PermutationNull(seed=42)
        samples = null.sample(TRADES_10, n=10, rng=rng)
        result = estimate_timing(TRADES_10, samples, rng)
        for key in ("mean", "std", "model_regret", "null_regret_mean"):
            assert key in result

    def test_empty_null_trajectories(self):
        result = estimate_timing(TRADES_10, [])
        assert result["mean"] == 0.0

    def test_missing_bar_index_defaults_to_zero(self):
        trades_no_index = [{"side": 1, "net_pnl": 10.0}]
        result = estimate_timing(trades_no_index, [trades_no_index])
        assert isinstance(result["mean"], float)


class TestEstimateExecution:
    def test_model_cost_lower_than_null_is_positive_edge(self):
        model = [{"costs": 5.0}] * 10
        null_model = [{"costs": 15.0}] * 10
        null_samples = [null_model] * 10
        result = estimate_execution(model, null_samples)
        assert result["mean"] > 0.0

    def test_model_cost_higher_than_null_is_negative_edge(self):
        model = [{"costs": 15.0}] * 10
        null_model = [{"costs": 5.0}] * 10
        null_samples = [null_model] * 10
        result = estimate_execution(model, null_samples)
        assert result["mean"] < 0.0

    def test_equal_costs_gives_zero_edge(self):
        model = [{"costs": 10.0}] * 10
        null_samples = [[{"costs": 10.0}] * 10] * 10
        result = estimate_execution(model, null_samples)
        assert abs(result["mean"]) < 1e-10

    def test_empty_null_samples(self):
        result = estimate_execution(TRADES_10, [])
        assert result["mean"] == 0.0


class TestEstimateStructural:
    def test_returns_expected_keys(self):
        rng = np.random.default_rng(42)
        null = PermutationNull(seed=42)
        samples = null.sample(TRADES_10_MIXED, n=5, rng=rng)
        result = estimate_structural(TRADES_10_MIXED, samples, rng)
        for key in ("mean", "std", "model_mi", "null_mi_mean"):
            assert key in result

    def test_single_trajectory_no_edge(self):
        result = estimate_structural([{"net_pnl": 10.0, "regime": "TRENDING"}],
                                      [[{"net_pnl": 10.0, "regime": "TRENDING"}]])
        assert result["mean"] == 0.0

    def test_fewer_than_3_null_samples_returns_zero(self):
        result = estimate_structural(TRADES_10_MIXED,
                                      [[{"net_pnl": 0.0, "regime": "RANGING"}]] * 2)
        assert result["mean"] == 0.0


# ═════════════════════════════════════════════════════════════════════
# 4. EIA Identifiability
# ═════════════════════════════════════════════════════════════════════

class TestEIA:
    def test_separability_above_threshold_passes(self):
        passed, margin = check_separability(0.5, 0.0, threshold=1e-6)
        assert passed is True
        assert margin == 0.5

    def test_separability_below_threshold_fails(self):
        passed, margin = check_separability(1e-9, 0.0, threshold=1e-6)
        assert passed is False

    def test_null_invariance_identical_estimates_passes(self):
        passed, var = check_null_invariance({"a": 0.5, "b": 0.5, "c": 0.5}, epsilon=0.1)
        assert passed is True
        assert var == 0.0

    def test_null_invariance_divergent_estimates_fails(self):
        passed, var = check_null_invariance({"a": 0.0, "b": 1.0}, epsilon=0.1)
        assert passed is False

    def test_null_invariance_zero_signal_trivially_passes(self):
        passed, var = check_null_invariance({"a": 0.0, "b": 0.0}, epsilon=0.1)
        assert passed is True

    def test_representation_stability_low_variance_passes(self):
        passed, std = check_representation_stability([0.5, 0.51, 0.49, 0.5, 0.5],
                                                      threshold_std_ratio=2.0)
        assert passed is True

    def test_representation_stability_high_variance_fails(self):
        passed, std = check_representation_stability([0.0, 1.0, 0.0, 1.0],
                                                      threshold_std_ratio=0.5)
        assert passed is False

    def test_representation_stability_zero_mean_trivially_passes(self):
        passed, std = check_representation_stability([0.0, 0.0, 0.0])
        assert passed is True

    def test_eia_all_pass(self):
        passed, details = eia_all_conditions(
            0.5,
            {"perm": 0.5, "block": 0.51},
            [0.5, 0.49, 0.51],
            separability_threshold=1e-6,
            null_invariance_epsilon=0.1,
            stability_threshold=2.0,
        )
        assert passed is True
        assert details["separability"]["passed"] is True
        assert details["null_invariance"]["passed"] is True
        assert details["representation_stability"]["passed"] is True

    def test_eia_one_fails(self):
        passed, details = eia_all_conditions(
            0.5,
            {"perm": 0.0, "block": 1.0},
            [0.5, 0.49, 0.51],
            separability_threshold=1e-6,
            null_invariance_epsilon=0.1,
            stability_threshold=2.0,
        )
        assert passed is False
        assert details["null_invariance"]["passed"] is False


# ═════════════════════════════════════════════════════════════════════
# 5. EdgeInferenceEngine Integration
# ═════════════════════════════════════════════════════════════════════

class TestEdgeInferenceEngine:
    def test_deterministic_with_fixed_seed(self):
        engine = EdgeInferenceEngine(
            null_suite=[PermutationNull(seed=42), BlockNull(block_size=5, seed=42)],
            utility_fn=_identity_utility,
            n_null_samples=200,
            n_bootstrap=50,
            seed=42,
        )
        t1 = engine.infer(TRADES_10_MIXED)
        t2 = engine.infer(TRADES_10_MIXED)
        assert t1.to_dict() == t2.to_dict()

    def test_requires_at_least_one_null(self):
        with pytest.raises(ValueError, match="at least one"):
            EdgeInferenceEngine(null_suite=[], utility_fn=_identity_utility)

    def test_empty_null_suite_at_init_raises(self):
        with pytest.raises(ValueError):
            EdgeInferenceEngine([], lambda x: 0.0)

    def test_returns_valid_tensor_with_single_null(self):
        engine = EdgeInferenceEngine(
            null_suite=[PermutationNull(seed=42)],
            utility_fn=_identity_utility,
            n_null_samples=100,
            n_bootstrap=30,
            seed=42,
        )
        tensor = engine.infer(TRADES_10_MIXED)
        assert isinstance(tensor, EdgeTensor)
        comps = tensor.components()
        assert set(comps.keys()) == {"directional", "timing", "execution", "structural"}

    def test_determinism_across_seeds_is_not_identical(self):
        engine1 = EdgeInferenceEngine(
            null_suite=[PermutationNull(seed=1), BlockNull(block_size=5, seed=1)],
            utility_fn=_identity_utility,
            n_null_samples=100,
            n_bootstrap=30,
            seed=1,
        )
        engine2 = EdgeInferenceEngine(
            null_suite=[PermutationNull(seed=99), BlockNull(block_size=5, seed=99)],
            utility_fn=_identity_utility,
            n_null_samples=100,
            n_bootstrap=30,
            seed=99,
        )
        t1 = engine1.infer(TRADES_10_MIXED)
        t2 = engine2.infer(TRADES_10_MIXED)
        assert t1.to_dict() != t2.to_dict()

    def test_all_probabilities_in_01_range(self):
        engine = EdgeInferenceEngine(
            null_suite=[PermutationNull(seed=42)],
            utility_fn=_identity_utility,
            n_null_samples=100,
            n_bootstrap=30,
            seed=42,
        )
        tensor = engine.infer(TRADES_10_MIXED)
        for name, comp in tensor.components().items():
            assert 0.0 <= comp.mean <= 1.0, f"{name}: mean={comp.mean} outside [0,1]"

    def test_identifiability_flags_present(self):
        engine = EdgeInferenceEngine(
            null_suite=[PermutationNull(seed=42), BlockNull(block_size=5, seed=42)],
            utility_fn=_identity_utility,
            n_null_samples=200,
            n_bootstrap=50,
            seed=42,
        )
        tensor = engine.infer(TRADES_10_MIXED)
        id_map = tensor.identifiability()
        for name in ("directional", "timing", "execution", "structural"):
            assert name in id_map
            assert isinstance(id_map[name], bool)

    def test_utility_fn_impacts_result(self):
        def sum_pnl(traj):
            return sum(t.get("net_pnl", 0.0) for t in traj)

        def neg_sum_pnl(traj):
            return -sum(t.get("net_pnl", 0.0) for t in traj)

        engine_sum = EdgeInferenceEngine(
            null_suite=[PermutationNull(seed=42)],
            utility_fn=sum_pnl,
            n_null_samples=100,
            n_bootstrap=30,
            seed=42,
        )
        engine_neg = EdgeInferenceEngine(
            null_suite=[PermutationNull(seed=42)],
            utility_fn=neg_sum_pnl,
            n_null_samples=100,
            n_bootstrap=30,
            seed=42,
        )
        t_sum = engine_sum.infer(TRADES_10_MIXED)
        t_neg = engine_neg.infer(TRADES_10_MIXED)
        assert any(
            t_sum.components()[k].mean != t_neg.components()[k].mean
            for k in ("directional",)
        )

    def test_small_bootstrap_does_not_crash(self):
        engine = EdgeInferenceEngine(
            null_suite=[PermutationNull(seed=42)],
            utility_fn=_identity_utility,
            n_null_samples=20,
            n_bootstrap=5,
            seed=42,
        )
        tensor = engine.infer(TRADES_10_MIXED)
        assert isinstance(tensor, EdgeTensor)

    def test_single_trade_trajectory(self):
        single_trade = [TRADES_10[0]]
        engine = EdgeInferenceEngine(
            null_suite=[PermutationNull(seed=42)],
            utility_fn=_identity_utility,
            n_null_samples=50,
            n_bootstrap=20,
            seed=42,
        )
        tensor = engine.infer(single_trade)
        assert isinstance(tensor, EdgeTensor)
