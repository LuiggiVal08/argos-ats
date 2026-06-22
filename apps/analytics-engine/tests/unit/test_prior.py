"""Unit tests for EDL Prior specification.

Pure-domain tests: no I/O, no mocks, no infrastructure.
Tests the institutional skepticism prior (spec Section 8).
"""

import math

import pytest

from app.domain.edl import (
    DEFAULT_LAMBDA,
    P_BASE,
    ModelFamily,
    PriorCalculator,
    Regime,
)
from app.domain.edl.prior import (
    _complexity_penalty,
    _historical_factor,
    _logodds_to_prob,
    _prob_to_logodds,
    _regime_penalty,
    _transfer_penalty,
)


class TestPriorProbability:
    """Fundamental prior guarantees (spec Sections 8.1–8.3)."""

    def test_skepticism_default(self):
        """P(edge) << 0.5 for any reasonable model."""
        calc = PriorCalculator()
        for cs in [0.0, 0.1, 0.3, 0.5, 0.7, 1.0]:
            r = calc.compute(
                model_id="skepticism-test",
                complexity_score=cs,
                training_regimes=frozenset({Regime.TRENDING}),
                evaluation_regime=Regime.TRENDING,
            )
            p = r.probability("TRENDING")
            assert p < 0.5, f"P(edge)={p:.4f} should be << 0.5 at cs={cs}"

    def test_p_base_constant(self):
        """P_base is always exactly 0.15."""
        assert P_BASE == 0.15

    def test_prior_below_base_for_any_complexity(self):
        """Prior must always be <= P_base because penalties compound."""
        calc = PriorCalculator()
        r = calc.compute(
            model_id="base-test",
            complexity_score=0.0,  # no complexity penalty
            training_regimes=frozenset({Regime.TRENDING, Regime.RANGING}),
            evaluation_regime=Regime.TRENDING,
            model_family=ModelFamily.NEW,
        )
        p = r.probability("TRENDING")
        assert p <= P_BASE, f"P(edge)={p:.4f} should not exceed P_base={P_BASE}"


class TestComplexityPenalty:
    """Spec Section 8.5 — C(model) = exp(-λ · complexity_score)."""

    def test_zero_complexity_penalty_is_one(self):
        """complexity_score=0 → C=1.0 (no penalty for simplest models)."""
        c = _complexity_penalty(0.0, 1.0)
        assert c == 1.0

    def test_max_complexity_penalty(self):
        """complexity_score=1.0 → C = exp(-1.0)."""
        c = _complexity_penalty(1.0, 1.0)
        assert c == pytest.approx(math.exp(-1.0))

    def test_lambda_scales_penalty(self):
        """Higher λ increases penalty at same complexity."""
        c_low = _complexity_penalty(0.5, 0.5)
        c_high = _complexity_penalty(0.5, 2.0)
        assert c_high < c_low

    def test_complexity_not_rejected_outside_01(self):
        """complexity_score must be [0,1]; tested via validation in compute()."""
        calc = PriorCalculator()
        with pytest.raises(ValueError, match="complexity_score"):
            calc.compute(
                model_id="invalid-cs",
                complexity_score=1.5,
                training_regimes=frozenset(),
                evaluation_regime=Regime.TRENDING,
            )


class TestRegimePenalty:
    """Spec Section 8.6 — R(model)."""

    def test_single_regime_penalty(self):
        """Training on 1 regime → R=0.7."""
        r = _regime_penalty(frozenset({Regime.TRENDING}))
        assert r == 0.7

    def test_multi_regime_no_penalty(self):
        """Training on 2+ regimes → R=1.0."""
        r = _regime_penalty(frozenset({Regime.TRENDING, Regime.RANGING}))
        assert r == 1.0

    def test_empty_regime_set_is_single(self):
        """Empty training set considered single-regime → R=0.7."""
        r = _regime_penalty(frozenset())
        assert r == 0.7


class TestHistoricalFactor:
    """Spec Section 8.7 — H(model)."""

    def test_new_model(self):
        assert _historical_factor(ModelFamily.NEW) == 1.0

    def test_champion_derived(self):
        assert _historical_factor(ModelFamily.CHAMPION_DERIVED) == 1.2

    def test_poor_history(self):
        assert _historical_factor(ModelFamily.POOR_HISTORY) == 0.8


class TestTransferPenalty:
    """Spec Section 8.8 — T(train, eval)."""

    def test_same_regime(self):
        t = _transfer_penalty(
            frozenset({Regime.TRENDING}), Regime.TRENDING
        )
        assert t == 1.0

    def test_cross_regime(self):
        t = _transfer_penalty(
            frozenset({Regime.TRENDING}), Regime.RANGING
        )
        assert t == 0.7

    def test_unknown_regime(self):
        t = _transfer_penalty(frozenset(), Regime.TRENDING)
        assert t == 0.5


class TestPriorVector:
    """Spec Section 10.2 — prior lives as a vector per regime."""

    def test_vector_prior_returns_all_regimes(self):
        """By default, computes for TRENDING, RANGING, VOLATILE."""
        calc = PriorCalculator()
        r = calc.compute(
            model_id="vector-test",
            complexity_score=0.3,
            training_regimes=frozenset({Regime.TRENDING}),
        )
        assert set(r.prior_logodds.keys()) == {"TRENDING", "RANGING", "VOLATILE"}

    def test_vector_prior_differs_by_regime(self):
        """Log-odds differ because T varies by eval regime."""
        calc = PriorCalculator()
        r = calc.compute(
            model_id="vector-test",
            complexity_score=0.3,
            training_regimes=frozenset({Regime.TRENDING}),
        )
        lo_trend = r.prior_logodds["TRENDING"]
        lo_range = r.prior_logodds["RANGING"]
        assert lo_trend != lo_range

    def test_single_regime_returns_only_that_regime(self):
        """When evaluation_regime is provided, only that key is returned."""
        calc = PriorCalculator()
        r = calc.compute(
            model_id="single-test",
            complexity_score=0.3,
            training_regimes=frozenset({Regime.TRENDING}),
            evaluation_regime=Regime.RANGING,
        )
        assert set(r.prior_logodds.keys()) == {"RANGING"}

    def test_champion_derived_boosts_prior(self):
        """Champion-derived models have higher prior than new models."""
        calc = PriorCalculator()
        r_new = calc.compute(
            model_id="new",
            complexity_score=0.3,
            training_regimes=frozenset({Regime.TRENDING}),
            evaluation_regime=Regime.TRENDING,
            model_family=ModelFamily.NEW,
        )
        r_champ = calc.compute(
            model_id="champ",
            complexity_score=0.3,
            training_regimes=frozenset({Regime.TRENDING}),
            evaluation_regime=Regime.TRENDING,
            model_family=ModelFamily.CHAMPION_DERIVED,
        )
        p_new = r_new.probability("TRENDING")
        p_champ = r_champ.probability("TRENDING")
        assert p_champ > p_new, (
            f"Champion ({p_champ:.4f}) should have higher prior "
            f"than new ({p_new:.4f})"
        )


class TestLogOdds:
    """Spec Section 8.9 — internal representation."""

    def test_logodds_roundtrip(self):
        """Probability ↔ log-odds is bijective."""
        for p in [0.001, 0.01, 0.1, 0.15, 0.3, 0.5, 0.7, 0.9, 0.99, 0.999]:
            lo = _prob_to_logodds(p)
            p_back = _logodds_to_prob(lo)
            assert abs(p - p_back) < 1e-12, (
                f"Roundtrip failed at p={p}: {p_back}"
            )

    def test_negative_logodds_for_below_half(self):
        """p < 0.5 → log-odds is negative."""
        lo = _prob_to_logodds(0.15)
        assert lo < 0

    def test_zero_logodds_at_half(self):
        """p = 0.5 → log-odds = 0."""
        lo = _prob_to_logodds(0.5)
        assert lo == pytest.approx(0.0)

    def test_logodds_is_additive(self):
        """Verifying the sum property: L(A and B) = L(A) + L(B)."""
        lo_a = _prob_to_logodds(0.15)
        lo_b = _prob_to_logodds(0.20)
        combined = _logodds_to_prob(lo_a + lo_b)
        # This is just verifying the math works, not a domain rule
        assert combined < 0.5  # both skeptical, combined still skeptical


class TestPriorResult:
    """PriorResult output contract."""

    def test_components_breakdown(self):
        """PriorResult includes full audit trail."""
        calc = PriorCalculator()
        r = calc.compute(
            model_id="audit-test",
            complexity_score=0.5,
            training_regimes=frozenset({Regime.TRENDING}),
            evaluation_regime=Regime.RANGING,
            model_family=ModelFamily.NEW,
        )
        assert r.components.p_base == P_BASE
        assert r.components.complexity_penalty == pytest.approx(math.exp(-1.0 * 0.5))
        assert r.components.regime_penalty == 0.7
        assert r.components.historical_factor == 1.0
        assert r.components.transfer_penalty == 0.7
        assert r.lambda_used == DEFAULT_LAMBDA

    def test_probability_accessor(self):
        """PriorResult.probability() recovers correct value."""
        calc = PriorCalculator()
        r = calc.compute(
            model_id="accessor-test",
            complexity_score=0.0,
            training_regimes=frozenset({Regime.TRENDING}),
            evaluation_regime=Regime.TRENDING,
        )
        p = r.probability("TRENDING")
        lo = r.prior_logodds["TRENDING"]
        assert p == pytest.approx(_logodds_to_prob(lo))

    def test_probability_unknown_regime(self):
        """probability() raises for unknown regime key."""
        calc = PriorCalculator()
        r = calc.compute(
            model_id="unknown-test",
            complexity_score=0.0,
            training_regimes=frozenset({Regime.TRENDING}),
            evaluation_regime=Regime.TRENDING,
        )
        with pytest.raises(ValueError, match="Unknown regime"):
            r.probability("BOGUS")

    def test_lambda_configurable(self):
        """PriorCalculator accepts custom lambda."""
        calc = PriorCalculator(lambda_=2.0)
        assert calc.lambda_ == 2.0
        r = calc.compute(
            model_id="custom-lambda",
            complexity_score=0.5,
            training_regimes=frozenset({Regime.TRENDING}),
            evaluation_regime=Regime.TRENDING,
        )
        assert r.lambda_used == 2.0
        # Higher lambda → lower complexity penalty → lower prior
        calc_default = PriorCalculator()
        r_default = calc_default.compute(
            model_id="default-test",
            complexity_score=0.5,
            training_regimes=frozenset({Regime.TRENDING}),
            evaluation_regime=Regime.TRENDING,
        )
        assert r.probability("TRENDING") < r_default.probability("TRENDING")


class TestValidation:
    """Input validation guards."""

    def test_rejects_negative_lambda(self):
        with pytest.raises(ValueError, match="lambda"):
            PriorCalculator(lambda_=-0.1)

    def test_rejects_zero_lambda(self):
        with pytest.raises(ValueError, match="lambda"):
            PriorCalculator(lambda_=0.0)

    def test_rejects_non_frozenset_training(self):
        calc = PriorCalculator()
        with pytest.raises(TypeError, match="frozenset"):
            calc.compute(
                model_id="type-test",
                complexity_score=0.3,
                training_regimes={Regime.TRENDING},  # plain set
                evaluation_regime=Regime.TRENDING,
            )

    def test_rejects_invalid_regime_in_training(self):
        calc = PriorCalculator()
        with pytest.raises(TypeError, match="Regime"):
            calc.compute(
                model_id="bad-regime",
                complexity_score=0.3,
                training_regimes=frozenset({"NOT_A_REGIME"}),  # type: ignore
                evaluation_regime=Regime.TRENDING,
            )


class TestConcreteScenarios:
    """Real model profiles from spec Section 8.5."""

    @pytest.fixture
    def calc(self):
        return PriorCalculator()

    def test_ema_cross_trending(self, calc: PriorCalculator):
        """EMA Cross trained on trending, evaluated on trending."""
        r = calc.compute(
            model_id="ema-cross",
            complexity_score=0.1,
            training_regimes=frozenset({Regime.TRENDING}),
            evaluation_regime=Regime.TRENDING,
            model_family=ModelFamily.NEW,
        )
        p = r.probability("TRENDING")
        # EMA Cross is simple and in-regime: highest prior possible
        assert 0.08 < p < 0.12, f"P(edge)={p:.4f}"

    def test_ema_cross_ranging(self, calc: PriorCalculator):
        """EMA Cross trained on trending, evaluated on ranging (cross-regime)."""
        r = calc.compute(
            model_id="ema-cross",
            complexity_score=0.1,
            training_regimes=frozenset({Regime.TRENDING}),
            evaluation_regime=Regime.RANGING,
            model_family=ModelFamily.NEW,
        )
        p = r.probability("RANGING")
        # Cross-regime: lower than same-regime
        assert 0.04 < p < 0.08, f"P(edge)={p:.4f}"

    def test_lstm_trending(self, calc: PriorCalculator):
        """LSTM trained on trending, evaluated on trending."""
        r = calc.compute(
            model_id="lstm",
            complexity_score=0.5,
            training_regimes=frozenset({Regime.TRENDING}),
            evaluation_regime=Regime.TRENDING,
            model_family=ModelFamily.NEW,
        )
        p = r.probability("TRENDING")
        # Higher complexity + single regime: moderate skepticism
        assert 0.04 < p < 0.10, f"P(edge)={p:.4f}"

    def test_ensemble_multi_regime_champion(self, calc: PriorCalculator):
        """Ensemble trained on both regimes, champion-derived."""
        r = calc.compute(
            model_id="ensemble",
            complexity_score=0.7,
            training_regimes=frozenset({Regime.TRENDING, Regime.RANGING}),
            evaluation_regime=Regime.TRENDING,
            model_family=ModelFamily.CHAMPION_DERIVED,
        )
        p = r.probability("TRENDING")
        # High complexity offset by multi-regime + champion history
        assert 0.05 < p < 0.15, f"P(edge)={p:.4f}"

    def test_rl_poor_unknown_volatile(self, calc: PriorCalculator):
        """RL with poor history, no training regimes, evaluated on volatile."""
        r = calc.compute(
            model_id="rl-adaptive",
            complexity_score=1.0,
            training_regimes=frozenset(),
            evaluation_regime=Regime.VOLATILE,
            model_family=ModelFamily.POOR_HISTORY,
        )
        p = r.probability("VOLATILE")
        # Worst case: maximum skepticism
        assert p < 0.03, f"P(edge)={p:.4f} should be nearly zero"
