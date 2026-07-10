"""ReplayAdapter — synchronous replay of the production inference pipeline.

Purpose: Measure consistency between research (raw model signal) and
production (full pipeline including RiskEngine) decisions.

Flow: OHLCV candles -> FeatureEngine -> scaler -> model.predict_proba ->
  threshold -> raw_signal (research decision) -> RiskEngine ->
  position_sizing -> production_decision

No asyncio, no websocket, no Redis, no real order execution.
"""
from __future__ import annotations

import json
import pickle
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import structlog

from ...domain.entities.risk_engine import (
    PortfolioState as RiskPortfolioState,
    RiskEngine,
    RiskVerdict,
)
from ...infrastructure.training.feature_engine import FeatureEngine

log = structlog.get_logger()

WARMUP_CANDLES = 200
RISK_PCT = Decimal("0.01")
SL_ATR_MULT = Decimal("2.0")
MAX_OPEN_POSITIONS = 1


@dataclass
class ReplayDecision:
    """Per-candle decision comparing research vs production pipeline."""
    timestamp: pd.Timestamp
    close: float
    prob_sell: float
    prob_hold: float
    prob_buy: float
    raw_signal: str
    production_signal: str
    position_size: float
    sl_price: float
    tp_price: float
    risk_verdict: str
    match: bool


@dataclass
class ReplayResult:
    """Aggregate result from a full replay run."""
    decisions: list[ReplayDecision] = field(default_factory=list)
    n_total: int = 0
    n_match: int = 0
    match_rate: float = 0.0
    n_holds_raw: int = 0
    n_buys_raw: int = 0
    n_sells_raw: int = 0
    n_holds_prod: int = 0
    n_buys_prod: int = 0
    n_sells_prod: int = 0
    n_risk_rejected: int = 0
    first_mismatch_idx: int = -1


class ReplayAdapter:
    """Synchronous replay of production inference over historical OHLCV.

    Loads model/scaler/metadata from a checkpoint directory, replays
    candles through FeatureEngine -> scaler -> model -> threshold ->
    RiskEngine, and records per-candle decisions comparing raw model
    signals against pipeline output.

    Attributes:
        is_loaded: True if model/scaler/metadata loaded successfully.
        load_error: Error message if loading failed.
        metadata: Loaded metadata dict (or None).
    """

    def __init__(
        self,
        model_dir: str | Path,
        symbol: str = "BTC/USDT",
        capital: float = 100_000.0,
    ) -> None:
        self._model_dir = Path(model_dir)
        self._base_symbol = symbol.split("/")[0].lower()
        self._pair_dir_name = symbol.replace("/", "_").lower()
        self._symbol = symbol
        self._capital = Decimal(str(capital))

        self._model: Any = None
        self._scaler: Any = None
        self._metadata: dict[str, Any] | None = None
        self._feature_names: list[str] = []
        self._buy_threshold: float = 0.50
        self._sell_threshold: float = 0.50
        self._loaded = False
        self._load_error: str = ""

        self._risk_engine = RiskEngine(
            max_consecutive_losses=3,
            max_open_positions=MAX_OPEN_POSITIONS,
            max_symbol_exposure_pct=Decimal("0.20"),
            max_total_exposure_pct=Decimal("0.90"),
            max_daily_drawdown_pct=Decimal("0.05"),
        )

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def load_error(self) -> str:
        return self._load_error

    @property
    def metadata(self) -> dict[str, Any] | None:
        return self._metadata

    def load(self) -> bool:
        """Load model, scaler, metadata from checkpoint.

        Expects model.pkl, scaler.pkl, metadata.json in
        ``model_dir/<pair_name>/``.
        """
        try:
            pair_dir = self._model_dir / self._pair_dir_name
            fallback = self._model_dir / self._base_symbol
            model_dir = pair_dir if pair_dir.exists() else fallback
            if not model_dir.exists():
                self._load_error = (
                    f"model directory not found: tried {pair_dir} and {fallback}"
                )
                return False

            meta_path = model_dir / "metadata.json"
            if not meta_path.exists():
                self._load_error = f"metadata.json not found in {model_dir}"
                return False
            self._metadata = json.loads(meta_path.read_text())

            model_path = model_dir / "model.pkl"
            if not model_path.exists():
                self._load_error = f"model.pkl not found in {model_dir}"
                return False
            self._model = pickle.loads(model_path.read_bytes())

            scaler_path = model_dir / "scaler.pkl"
            if not scaler_path.exists():
                self._load_error = f"scaler.pkl not found in {model_dir}"
                return False
            self._scaler = pickle.loads(scaler_path.read_bytes())

            self._feature_names = self._metadata.get("feature_names", [])
            if not self._feature_names:
                self._load_error = "metadata missing feature_names"
                return False

            params = self._metadata.get("parameters", {})
            thresholds = params.get("thresholds", {})
            self._buy_threshold = thresholds.get("BUY", 0.50)
            self._sell_threshold = thresholds.get("SELL", 0.50)

            self._loaded = True
            log.info(
                "replay_model_loaded",
                symbol=self._symbol,
                version=self._metadata.get("model_version", "unknown"),
                features=len(self._feature_names),
            )
            return True

        except Exception as e:
            self._load_error = str(e)
            log.error("replay_load_failed", error=str(e))
            return False

    def replay(
        self,
        ohlcv: pd.DataFrame,
        funding: pd.DataFrame | None = None,
    ) -> ReplayResult:
        """Run full replay over historical OHLCV.

        Args:
            ohlcv: DataFrame with columns open, high, low, close, volume.
                   Index should be a DatetimeIndex or contain a timestamp column.
            funding: Optional DataFrame with timestamp and fundingRate columns.

        Returns:
            ReplayResult with per-candle decisions and match statistics.
        """
        if not self._loaded:
            raise RuntimeError(f"ReplayAdapter not loaded: {self._load_error}")

        _ensure_ohlcv_columns(ohlcv)
        n = len(ohlcv)

        close = ohlcv["close"].values.astype(float)
        high = ohlcv["high"].values.astype(float)
        low = ohlcv["low"].values.astype(float)
        timestamps = ohlcv.index if isinstance(ohlcv.index, pd.DatetimeIndex) else pd.RangeIndex(n)

        features_df = FeatureEngine.compute_all(ohlcv, funding_df=funding)
        features = features_df[self._feature_names].values.astype(np.float64)
        atr_vals = _compute_atr(close, high, low, 14)

        decisions: list[ReplayDecision] = []
        portfolio_state = RiskPortfolioState(total_balance=self._capital)

        for i in range(WARMUP_CANDLES, n):
            ts = timestamps[i] if i < len(timestamps) else pd.NaT
            row = features[i: i + 1]
            scaled = self._scaler.transform(row)
            probs = self._model.predict_proba(scaled)[0]

            prob_sell = float(probs[0]) if len(probs) > 0 else 0.0
            prob_hold = float(probs[1]) if len(probs) > 1 else 0.0
            prob_buy = float(probs[2]) if len(probs) > 2 else 0.0

            # research decision: raw model signal
            raw_signal = "HOLD"
            if prob_buy > self._buy_threshold:
                raw_signal = "BUY"
            elif prob_sell > self._sell_threshold:
                raw_signal = "SELL"

            # production decision: raw signal -> RiskEngine -> position sizing
            production_signal = "HOLD"
            position_size = 0.0
            sl_price = 0.0
            tp_price = 0.0
            risk_verdict = "SKIPPED"

            if raw_signal != "HOLD":
                atr_now = atr_vals[i]
                if not np.isnan(atr_now) and atr_now > 0:
                    risk_amount = float(self._capital) * float(RISK_PCT)
                    sl_distance = max(atr_now * float(SL_ATR_MULT), 1e-10)

                    entry_price = close[i]
                    if raw_signal == "BUY":
                        sl_price = entry_price - sl_distance
                        tp_price = entry_price + sl_distance * 1.5
                    else:
                        sl_price = entry_price + sl_distance
                        tp_price = entry_price - sl_distance * 1.5

                    risk_per_unit = abs(entry_price - sl_price)
                    if risk_per_unit > 0:
                        position_size = risk_amount / risk_per_unit
                    else:
                        position_size = 0.0

                    assessment = self._risk_engine.assess(
                        portfolio_state, symbol=self._symbol,
                    )
                    risk_verdict = assessment.verdict.value

                    if assessment.verdict == RiskVerdict.APPROVED and position_size > 0:
                        production_signal = raw_signal
                    else:
                        position_size = 0.0
                        sl_price = 0.0
                        tp_price = 0.0
                else:
                    risk_verdict = "REJECTED_NO_ATR"

            match = raw_signal == production_signal
            decisions.append(ReplayDecision(
                timestamp=ts,
                close=close[i],
                prob_sell=prob_sell,
                prob_hold=prob_hold,
                prob_buy=prob_buy,
                raw_signal=raw_signal,
                production_signal=production_signal,
                position_size=position_size,
                sl_price=sl_price,
                tp_price=tp_price,
                risk_verdict=risk_verdict,
                match=match,
            ))

        n_match = sum(1 for d in decisions if d.match)
        n_total = len(decisions)
        match_rate = n_match / n_total if n_total > 0 else 1.0
        n_holds_raw = sum(1 for d in decisions if d.raw_signal == "HOLD")
        n_buys_raw = sum(1 for d in decisions if d.raw_signal == "BUY")
        n_sells_raw = sum(1 for d in decisions if d.raw_signal == "SELL")
        n_holds_prod = sum(1 for d in decisions if d.production_signal == "HOLD")
        n_buys_prod = sum(1 for d in decisions if d.production_signal == "BUY")
        n_sells_prod = sum(1 for d in decisions if d.production_signal == "SELL")
        n_risk_rejected = sum(
            1 for d in decisions
            if d.raw_signal != "HOLD" and d.production_signal == "HOLD"
            and d.risk_verdict not in ("SKIPPED", "REJECTED_NO_ATR")
        )

        first_mismatch = -1
        for j, d in enumerate(decisions):
            if not d.match:
                first_mismatch = j
                break

        return ReplayResult(
            decisions=decisions,
            n_total=n_total,
            n_match=n_match,
            match_rate=round(match_rate, 8),
            n_holds_raw=n_holds_raw,
            n_buys_raw=n_buys_raw,
            n_sells_raw=n_sells_raw,
            n_holds_prod=n_holds_prod,
            n_buys_prod=n_buys_prod,
            n_sells_prod=n_sells_prod,
            n_risk_rejected=n_risk_rejected,
            first_mismatch_idx=first_mismatch,
        )

    def to_dataframe(self, result: ReplayResult) -> pd.DataFrame:
        """Convert a ReplayResult to a DataFrame."""
        rows = []
        for d in result.decisions:
            rows.append({
                "timestamp": d.timestamp,
                "close": d.close,
                "prob_sell": d.prob_sell,
                "prob_hold": d.prob_hold,
                "prob_buy": d.prob_buy,
                "raw_signal": d.raw_signal,
                "production_signal": d.production_signal,
                "position_size": d.position_size,
                "sl_price": d.sl_price,
                "tp_price": d.tp_price,
                "risk_verdict": d.risk_verdict,
                "match": d.match,
            })
        return pd.DataFrame(rows)

    def report(self, result: ReplayResult) -> str:
        """Generate a text report from a ReplayResult."""
        lines = [
            "=" * 60,
            "REPLAY CONSISTENCY REPORT",
            "=" * 60,
            f"Total candles replayed:  {result.n_total}",
            f"Match rate:              {result.match_rate:.6%}",
            f"Matches:                 {result.n_match} / {result.n_total}",
            f"Mismatches:              {result.n_total - result.n_match}",
            f"RiskEngine rejections:   {result.n_risk_rejected}",
            "",
            "Decision Distribution:",
            f"  Research (raw model):  HOLD={result.n_holds_raw}  BUY={result.n_buys_raw}  SELL={result.n_sells_raw}",
            f"  Production (pipeline): HOLD={result.n_holds_prod}  BUY={result.n_buys_prod}  SELL={result.n_sells_prod}",
            "",
            "First mismatch index:",
            f"  {result.first_mismatch_idx}",
            "",
        ]
        if result.n_total - result.n_match > 0 and result.first_mismatch_idx >= 0:
            d = result.decisions[result.first_mismatch_idx]
            lines.extend([
                "First Mismatch Detail:",
                f"  timestamp:           {d.timestamp}",
                f"  raw_signal:          {d.raw_signal}",
                f"  production_signal:   {d.production_signal}",
                f"  prob_buy / prob_hold / prob_sell: {d.prob_buy:.4f} / {d.prob_hold:.4f} / {d.prob_sell:.4f}",
                f"  risk_verdict:        {d.risk_verdict}",
                f"  position_size:       {d.position_size:.6f}",
                f"  sl_price / tp_price: {d.sl_price:.2f} / {d.tp_price:.2f}",
            ])
        lines.append("=" * 60)
        return "\n".join(lines)


# ── Helpers ───────────────────────────────────────────────────────

def _compute_atr(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    period: int = 14,
) -> np.ndarray:
    """Wilder's smoothed ATR (matches Phase 4 simulation exactly)."""
    tr = np.maximum(
        high[1:] - low[1:],
        np.abs(high[1:] - close[:-1]),
        np.abs(low[1:] - close[:-1]),
    )
    atr = np.full(len(close), np.nan)
    if len(close) <= period:
        return atr
    atr[period] = np.mean(tr[1: period + 1])
    for i in range(period + 1, len(close)):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i - 1]) / period
    return atr


def _ensure_ohlcv_columns(df: pd.DataFrame) -> None:
    required = {"open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"OHLCV DataFrame missing columns: {missing}")
