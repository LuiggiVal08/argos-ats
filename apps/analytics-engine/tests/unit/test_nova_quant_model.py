"""Unit tests for NovaQuantModel domain entity."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.domain.entities.nova_quant_model import (
    FeatureMismatchError,
    ModelVersionMismatchError,
    NovaQuantModel,
    StaleModelError,
)
from app.domain.value_objects.model_config import ModelConfig


def _make_model(
    trained_at: datetime | None = None,
    n_features: int | None = None,
    **kwargs,
) -> NovaQuantModel:
    cfg = ModelConfig()
    nf = n_features or len(cfg.features)
    return NovaQuantModel(
        config=cfg,
        model_version=kwargs.get("model_version", "1.0.0"),
        trained_at=trained_at or datetime.now(timezone.utc),
        weights_hash=kwargs.get("weights_hash", "abc123"),
        feature_means=tuple(float(i) for i in range(nf)),
        feature_stds=tuple(float(i + 1) for i in range(nf)),
        metrics=kwargs.get("metrics", {"val_loss": 0.35}),
    )


class TestNovaQuantModelConstruction:
    def test_minimal_creation(self) -> None:
        cfg = ModelConfig()
        model = NovaQuantModel(
            config=cfg,
            model_version="1.0.0",
            trained_at=datetime.now(timezone.utc),
            weights_hash="abc123",
            feature_means=tuple(float(i) for i in range(len(cfg.features))),
            feature_stds=tuple(float(i + 1) for i in range(len(cfg.features))),
        )
        assert model.model_version == "1.0.0"
        assert model.weights_hash == "abc123"
        assert model.metrics == {}

    def test_feature_count_mismatch_means(self) -> None:
        cfg = ModelConfig()
        with pytest.raises(FeatureMismatchError, match="feature_means"):
            NovaQuantModel(
                config=cfg,
                model_version="1.0.0",
                trained_at=datetime.now(timezone.utc),
                weights_hash="x",
                feature_means=(1.0, 2.0),  # 2 pero cfg tiene 20
                feature_stds=tuple(float(i) for i in range(len(cfg.features))),
            )

    def test_feature_count_mismatch_stds(self) -> None:
        cfg = ModelConfig()
        with pytest.raises(FeatureMismatchError, match="feature_stds"):
            NovaQuantModel(
                config=cfg,
                model_version="1.0.0",
                trained_at=datetime.now(timezone.utc),
                weights_hash="x",
                feature_means=tuple(float(i) for i in range(len(cfg.features))),
                feature_stds=(1.0, 2.0),
            )

    def test_zero_std_rejected(self) -> None:
        cfg = ModelConfig()
        nf = len(cfg.features)
        with pytest.raises(FeatureMismatchError, match="positive"):
            NovaQuantModel(
                config=cfg,
                model_version="1.0.0",
                trained_at=datetime.now(timezone.utc),
                weights_hash="x",
                feature_means=tuple(float(i) for i in range(nf)),
                feature_stds=tuple(0.0 for _ in range(nf)),
            )

    def test_empty_weights_hash_rejected(self) -> None:
        cfg = ModelConfig()
        nf = len(cfg.features)
        with pytest.raises(ValueError, match="weights_hash"):
            NovaQuantModel(
                config=cfg,
                model_version="1.0.0",
                trained_at=datetime.now(timezone.utc),
                weights_hash="",
                feature_means=tuple(float(i) for i in range(nf)),
                feature_stds=tuple(float(i + 1) for i in range(nf)),
            )

    def test_empty_version_rejected(self) -> None:
        cfg = ModelConfig()
        nf = len(cfg.features)
        with pytest.raises(ValueError, match="model_version"):
            NovaQuantModel(
                config=cfg,
                model_version="",
                trained_at=datetime.now(timezone.utc),
                weights_hash="x",
                feature_means=tuple(float(i) for i in range(nf)),
                feature_stds=tuple(float(i + 1) for i in range(nf)),
            )


class TestNovaQuantModelProperties:
    def test_properties_match_construction(self) -> None:
        cfg = ModelConfig()
        nf = len(cfg.features)
        trained_at = datetime(2026, 6, 1, tzinfo=timezone.utc)
        model = NovaQuantModel(
            config=cfg,
            model_version="2.0.0",
            trained_at=trained_at,
            weights_hash="def789",
            feature_means=tuple(float(i) for i in range(nf)),
            feature_stds=tuple(float(i + 1) for i in range(nf)),
            metrics={"sharpe": 1.5, "val_loss": 0.3},
        )
        assert model.config is cfg
        assert model.model_version == "2.0.0"
        assert model.trained_at == trained_at
        assert model.weights_hash == "def789"
        assert model.metrics == {"sharpe": 1.5, "val_loss": 0.3}

    def test_metrics_defensive_copy(self) -> None:
        model = _make_model()
        metrics = model.metrics
        metrics["new_key"] = 1.0  # noqa
        assert "new_key" not in model.metrics

    def test_age_days_recent(self) -> None:
        model = _make_model(trained_at=datetime.now(timezone.utc))
        assert model.age_days < 0.01

    def test_age_days_old(self) -> None:
        trained_at = datetime.now(timezone.utc) - timedelta(days=3)
        model = _make_model(trained_at=trained_at)
        assert 2.9 < model.age_days < 3.1

    def test_is_stale_false_recent(self) -> None:
        model = _make_model(trained_at=datetime.now(timezone.utc))
        assert model.is_stale is False

    def test_is_stale_true_old(self) -> None:
        trained_at = datetime.now(timezone.utc) - timedelta(days=10)
        model = _make_model(trained_at=trained_at)
        assert model.is_stale is True

    def test_repr(self) -> None:
        model = _make_model(model_version="3.0.0")
        r = repr(model)
        assert "NovaQuantModel" in r
        assert "3.0.0" in r


class TestNovaQuantModelValidation:
    def test_validate_input_ok(self) -> None:
        model = _make_model()
        nf = len(model.config.features)
        model.validate_input(nf)  # no error

    def test_validate_input_fail(self) -> None:
        model = _make_model()
        with pytest.raises(FeatureMismatchError):
            model.validate_input(3)

    def test_assert_not_stale_ok(self) -> None:
        model = _make_model(trained_at=datetime.now(timezone.utc))
        model.assert_not_stale()  # no error

    def test_assert_not_stale_fail(self) -> None:
        trained_at = datetime.now(timezone.utc) - timedelta(days=10)
        model = _make_model(trained_at=trained_at)
        with pytest.raises(StaleModelError):
            model.assert_not_stale()

    def test_assert_version_ok(self) -> None:
        model = _make_model(model_version="2.0.0")
        model.assert_version("2.0.0")  # no error

    def test_assert_version_fail(self) -> None:
        model = _make_model(model_version="2.0.0")
        with pytest.raises(ModelVersionMismatchError):
            model.assert_version("1.0.0")
