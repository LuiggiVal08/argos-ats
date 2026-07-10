"""Tests for schema.py — PO-Layer metric event emitter."""
from app.observability.schema import emit_metric, MetricEvent, METRIC_LEVELS


class TestMetricLevels:
    def test_valid_levels(self):
        assert "INFO" in METRIC_LEVELS
        assert "WARNING" in METRIC_LEVELS
        assert "CRITICAL" in METRIC_LEVELS
        assert "FREEZE" in METRIC_LEVELS

    def test_unknown_level_falls_back_to_info(self):
        # Should not raise — unknown levels become INFO
        emit_metric("test.metric", value=42, level="UNKNOWN")


class TestMetricEvent:
    def test_to_log_includes_all_fields(self):
        event = MetricEvent(
            metric="test.metric",
            value=42,
            threshold=">0",
            level="INFO",
        )
        payload = event.to_log()
        assert payload["event"] == "po_layer_metric"
        assert payload["metric"] == "test.metric"
        assert payload["value"] == 42
        assert payload["threshold"] == ">0"
        assert payload["level"] == "INFO"
        assert "timestamp" in payload


class TestEmitMetric:
    def test_emit_info(self):
        # Smoke test: should not raise
        emit_metric("test.info", value="ok", threshold="n/a", level="INFO")

    def test_emit_warning(self):
        emit_metric("test.warning", value="degraded", threshold="0", level="WARNING")

    def test_emit_critical(self):
        emit_metric("test.critical", value="fail", threshold="0", level="CRITICAL")

    def test_emit_freeze(self):
        emit_metric("test.freeze", value="halt", threshold="0%", level="FREEZE")

    def test_emit_with_extra_context(self):
        emit_metric(
            "test.extra",
            value="val",
            threshold="n/a",
            level="INFO",
            symbol="BTC/USDT",
            algo_id="abc-123",
            latency_ms=42.5,
        )
