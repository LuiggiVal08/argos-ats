"""InferenceTracker — longitudinal tracking, snapshots, and drift metrics.

Persists every inference result to a timeline CSV and generates periodic
snapshots at configurable inference intervals for distribution monitoring.
"""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import structlog

log = structlog.get_logger()

_SNAPSHOT_INTERVAL = 24


class InferenceTracker:
    """Tracks inferences longitudinally for distribution monitoring.

    Responsibilities:
    - Append rows to ``inference_timeline.csv``
    - Generate markdown snapshots every N inferences
    - Compute rolling slope metrics (hold_slope_24, sell_slope_24, etc.)
    - Read/write the inference counter for ``inference_sequence_id``
    """

    def __init__(
        self,
        timeline_path: str | Path,
        snapshots_dir: str | Path,
        state_dir: str | Path,
        symbol: str = "BTC/USDT",
    ) -> None:
        self._timeline_path = Path(timeline_path)
        self._snapshots_dir = Path(snapshots_dir)
        self._state_dir = Path(state_dir)
        self._symbol = symbol
        self._counter_file = self._state_dir / "inference_counter.json"

        self._timeline_path.parent.mkdir(parents=True, exist_ok=True)
        self._snapshots_dir.mkdir(parents=True, exist_ok=True)
        self._state_dir.mkdir(parents=True, exist_ok=True)

        self._last_snapshot_count = self._find_last_snapshot_count()

    def _read_counter(self) -> int:
        try:
            with open(self._counter_file) as f:
                return int(json.load(f).get("counter", 0))
        except (FileNotFoundError, ValueError, json.JSONDecodeError):
            return 0

    def _write_counter(self, count: int) -> None:
        try:
            with open(self._counter_file, "w") as f:
                json.dump(
                    {"counter": count, "updated": datetime.now(timezone.utc).isoformat()},
                    f,
                )
        except OSError as e:
            log.warning("counter_write_failed", path=str(self._counter_file), error=str(e))

    def next_sequence_id(self) -> str:
        """Atomically increment the counter and return INF-NNNNNN."""
        count = self._read_counter() + 1
        self._write_counter(count)
        return f"INF-{count:06d}"

    def append(
        self,
        *,
        sequence_id: str,
        timestamp_utc: str,
        candle_open: float,
        candle_close: float,
        prob_sell: float,
        prob_hold: float,
        prob_buy: float,
        decision: str,
        ema_fast: float | None = None,
        bb_middle: float | None = None,
        volume_sma: float | None = None,
        obv: float | None = None,
        market_regime: str = "UNKNOWN",
        position_open: bool = False,
        trade_opened: bool = False,
        trade_id: str = "",
        model_version: str = "",
        model_checksum: str = "",
    ) -> None:
        """Append one row to the inference timeline CSV.

        Creates the CSV with headers on first write.
        """
        write_header = not self._timeline_path.exists()
        try:
            with open(self._timeline_path, "a", newline="") as f:
                writer = csv.writer(f)
                if write_header:
                    writer.writerow([
                        "sequence_id",
                        "timestamp_utc",
                        "candle_open",
                        "candle_close",
                        "sell_probability",
                        "hold_probability",
                        "buy_probability",
                        "decision",
                        "ema_fast",
                        "bb_middle",
                        "volume_sma",
                        "obv",
                        "market_regime",
                        "position_open",
                        "trade_opened",
                        "trade_id",
                        "model_version",
                        "model_checksum",
                    ])
                writer.writerow([
                    sequence_id,
                    timestamp_utc,
                    f"{candle_open:.2f}",
                    f"{candle_close:.2f}",
                    f"{prob_sell:.6f}",
                    f"{prob_hold:.6f}",
                    f"{prob_buy:.6f}",
                    decision,
                    f"{ema_fast:.2f}" if ema_fast is not None else "",
                    f"{bb_middle:.2f}" if bb_middle is not None else "",
                    f"{volume_sma:.2f}" if volume_sma is not None else "",
                    f"{obv:.2f}" if obv is not None else "",
                    market_regime,
                    str(position_open),
                    str(trade_opened),
                    trade_id,
                    model_version,
                    model_checksum,
                ])
        except OSError as e:
            log.warning("timeline_append_failed", path=str(self._timeline_path), error=str(e))

        # Check snapshot boundary
        seq_num = int(sequence_id.split("-")[1])
        if seq_num > 0 and seq_num % _SNAPSHOT_INTERVAL == 0:
            self._generate_snapshot(seq_num)

    # ──────────────────────────────────────────────
    # Snapshot generation
    # ──────────────────────────────────────────────

    def _find_last_snapshot_count(self) -> int:
        if not self._snapshots_dir.exists():
            return 0
        counts = []
        for f in self._snapshots_dir.glob("snapshot_*.md"):
            try:
                counts.append(int(f.stem.split("_")[1]))
            except (IndexError, ValueError):
                continue
        return max(counts) if counts else 0

    def _generate_snapshot(self, seq_num: int) -> None:
        """Generate a markdown snapshot at the current inference count."""
        data = self._read_timeline()
        if not data:
            log.warning("snapshot_skipped_no_data", seq_num=seq_num)
            return

        probs_sell = np.array([r["sell_probability"] for r in data])
        probs_hold = np.array([r["hold_probability"] for r in data])
        probs_buy = np.array([r["buy_probability"] for r in data])
        decisions = [r["decision"] for r in data]

        n = len(data)
        n_holds = sum(1 for d in decisions if d == "HOLD")
        n_buys = sum(1 for d in decisions if d == "BUY")
        n_sells = sum(1 for d in decisions if d == "SELL")

        # Slopes: hold_slope_24, hold_slope_48, sell_slope_24, buy_slope_24
        hold_slope_24, hold_slope_48, sell_slope_24, buy_slope_24 = self._compute_slopes(probs_hold, probs_sell, probs_buy)

        # Expected trades from Phase 3.9 research rate (4.22%)
        research_trade_rate = 0.0422
        expected_trades = research_trade_rate * n
        actual_signals = n_buys + n_sells

        # Cumulative distribution percentiles
        def pctl(arr, p):
            return float(np.percentile(arr, p)) if len(arr) > 0 else 0.0

        content = f"""# Snapshot {seq_num} — Inference Distribution Monitor

**Generated**: {datetime.now(timezone.utc).isoformat()}
**Symbol**: {self._symbol}
**Total inferences**: {n}

---

## Cumulative Distribution

| Statistic | P(SELL) | P(HOLD) | P(BUY) |
|-----------|---------|---------|--------|
| Mean | {float(np.mean(probs_sell)):.6f} | {float(np.mean(probs_hold)):.6f} | {float(np.mean(probs_buy)):.6f} |
| Std | {float(np.std(probs_sell, ddof=1)):.6f} | {float(np.std(probs_hold, ddof=1)):.6f} | {float(np.std(probs_buy, ddof=1)):.6f} |
| Median | {pctl(probs_sell, 50):.6f} | {pctl(probs_hold, 50):.6f} | {pctl(probs_buy, 50):.6f} |
| P5 | {pctl(probs_sell, 5):.6f} | {pctl(probs_hold, 5):.6f} | {pctl(probs_buy, 5):.6f} |
| P25 | {pctl(probs_sell, 25):.6f} | {pctl(probs_hold, 25):.6f} | {pctl(probs_buy, 25):.6f} |
| P75 | {pctl(probs_sell, 75):.6f} | {pctl(probs_hold, 75):.6f} | {pctl(probs_buy, 75):.6f} |
| P95 | {pctl(probs_sell, 95):.6f} | {pctl(probs_hold, 95):.6f} | {pctl(probs_buy, 95):.6f} |
| Min | {float(np.min(probs_sell)):.6f} | {float(np.min(probs_hold)):.6f} | {float(np.min(probs_buy)):.6f} |
| Max | {float(np.max(probs_sell)):.6f} | {float(np.max(probs_hold)):.6f} | {float(np.max(probs_buy)):.6f} |

---

## Decision Breakdown

| Decision | Count | Percentage |
|----------|-------|-----------|
| HOLD | {n_holds} | {100 * n_holds / n:.1f}% |
| SELL | {n_sells} | {100 * n_sells / n:.1f}% |
| BUY | {n_buys} | {100 * n_buys / n:.1f}% |

---

## Temporal Trends

| Metric | Value |
|--------|-------|
| hold_slope_24 | {hold_slope_24:.6f} |
| hold_slope_48 | {hold_slope_48:.6f} |
| sell_slope_24 | {sell_slope_24:.6f} |
| buy_slope_24 | {buy_slope_24:.6f} |

### Interpretation
- **hold_slope_24 > 0**: P(HOLD) increasing → model converging toward HOLD
- **hold_slope_24 < 0**: P(HOLD) decreasing → model diverging from HOLD
- **sell_slope_24 > 0**: SELL signals becoming more likely
- **buy_slope_24 > 0**: BUY signals becoming more likely

---

## Trade Expectation vs Reality

| Metric | Value |
|--------|-------|
| Expected trades (Phase 3.9 rate) | {expected_trades:.2f} |
| Actual signals generated | {actual_signals} |
| Actual trade executions | — |
| Gap | {actual_signals - expected_trades:.2f} |

**Phase 3.9 baseline trade rate**: 4.22% of hourly decisions

---

## Convergence Assessment

```
{'CONVERGING_TOWARD_SIGNALS' if hold_slope_24 < -0.001 else
 'STABILIZED_IN_HOLD' if abs(hold_slope_24) < 0.001 and float(np.mean(probs_hold)) > 0.80 else
 'DIVERGING_TOWARD_EXTREME' if hold_slope_24 > 0.001 and float(np.mean(probs_hold)) > 0.90 else
 'MONITORING'}
```

Last 5 inferences:

| Seq | Time | P(SELL) | P(HOLD) | P(BUY) | Decision |
|-----|------|---------|---------|--------|----------|
"""
        # Last 5 entries
        for r in data[-5:]:
            content += f"| {r['sequence_id']} | {r['timestamp_utc']} | {r['sell_probability']:.4f} | {r['hold_probability']:.4f} | {r['buy_probability']:.4f} | {r['decision']} |\n"

        try:
            snap_path = self._snapshots_dir / f"snapshot_{seq_num:03d}.md"
            with open(snap_path, "w") as f:
                f.write(content)
            log.info("snapshot_generated", path=str(snap_path), inferences=n)
        except OSError as e:
            log.warning("snapshot_write_failed", path=str(snap_path), error=str(e))

    # ──────────────────────────────────────────────
    # Slope computation
    # ──────────────────────────────────────────────

    def _compute_slopes(
        self,
        probs_hold: np.ndarray,
        probs_sell: np.ndarray,
        probs_buy: np.ndarray,
    ) -> tuple[float, float, float, float]:
        """Compute rolling slopes via linear regression on recent windows.

        Returns:
            (hold_slope_24, hold_slope_48, sell_slope_24, buy_slope_24)
        """
        hold_slope_24 = self._slope(probs_hold, 24)
        hold_slope_48 = self._slope(probs_hold, 48)
        sell_slope_24 = self._slope(probs_sell, 24)
        buy_slope_24 = self._slope(probs_buy, 24)
        return hold_slope_24, hold_slope_48, sell_slope_24, buy_slope_24

    @staticmethod
    def _slope(values: np.ndarray, window: int) -> float:
        if len(values) < 2:
            return 0.0
        y = values[-min(window, len(values)):]
        x = np.arange(len(y))
        if np.std(x) == 0:
            return 0.0
        cov = np.cov(x, y, ddof=0)
        return float(cov[0, 1] / cov[0, 0])

    # ──────────────────────────────────────────────
    # Timeline reader
    # ──────────────────────────────────────────────

    def read_timeline(self) -> list[dict[str, Any]]:
        """Read the timeline CSV and return parsed rows."""
        return self._read_timeline()

    def _read_timeline(self) -> list[dict[str, Any]]:
        if not self._timeline_path.exists():
            return []
        rows = []
        try:
            with open(self._timeline_path, newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        rows.append({
                            "sequence_id": row.get("sequence_id", ""),
                            "timestamp_utc": row.get("timestamp_utc", ""),
                            "sell_probability": float(row.get("sell_probability", 0)),
                            "hold_probability": float(row.get("hold_probability", 0)),
                            "buy_probability": float(row.get("buy_probability", 0)),
                            "decision": row.get("decision", "UNKNOWN"),
                        })
                    except (ValueError, KeyError):
                        continue
        except OSError as e:
            log.warning("timeline_read_failed", path=str(self._timeline_path), error=str(e))
        return rows
