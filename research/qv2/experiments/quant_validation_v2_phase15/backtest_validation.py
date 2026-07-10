"""Phase 15b — Backtest Validation with Serialized Model (.pkl).

Train on 2023-06-01 → 2024-12-31 (18mo), backtest on 2025-01-01 → 2026-06-16 (18mo).
End-to-end: load .pkl → feature computation per bar → predict → trade → metrics.
Compares with Phase 11 lock test results.
"""

from __future__ import annotations

import json
import logging
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import RobustScaler
from sklearn.metrics import accuracy_score, f1_score

from experiments.quant_validation_v2.common import (
    BASE_FEATURES,
    FUNDING_FEATURES,
    compute_base_ta,
    compute_mtf_features,
    compute_funding_features,
    compute_vol_adj_returns,
    label_binary,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("phase15b")

DATA_DIR = Path(__file__).parent.parent.parent / "apps" / "analytics-engine" / "data"
MODELS_DIR = Path(__file__).parent.parent.parent / "models"
REPORT_DIR = Path(__file__).parent.parent.parent / "reports" / "quant_validation_v2_phase15"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

SYMBOL = "BTC"
EXCHANGE = "binance"
INITIAL_CAPITAL = 100_000.0
BUY_THRESHOLD = 0.60
SELL_THRESHOLD = 0.40
COST_PER_SIDE = 0.00155
COST_ROUND_TRIP = COST_PER_SIDE * 2
LOOKAHEAD = 5

TRAIN_START = "2023-06-01"
TRAIN_END = "2024-12-31"
TEST_START = "2025-01-01"
TEST_END = "2026-06-16"

MODEL_PARAMS = {
    "C": 0.1,
    "class_weight": "balanced",
    "solver": "liblinear",
    "max_iter": 5000,
    "random_state": 42,
}


@dataclass
class TradeRecord:
    entry_bar: int
    entry_ts: str
    exit_bar: int | None
    exit_ts: str | None
    side: int
    entry_price: float
    exit_price: float | None
    size: float
    gross_pnl: float
    entry_cost: float
    exit_cost: float
    net_pnl: float
    duration_bars: int | None
    exit_reason: str


@dataclass
class Position:
    side: int
    entry_bar: int
    entry_price: float
    size: float
    entry_cost: float


def load_ohlcv(symbol: str) -> pd.DataFrame:
    path = DATA_DIR / f"{symbol.lower()}_usdt_1h.parquet"
    df = pd.read_parquet(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


def load_funding(symbol: str) -> pd.DataFrame:
    path = DATA_DIR / f"{symbol.lower()}_funding_rates.parquet"
    df = pd.read_parquet(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


def build_X(ohlcv: pd.DataFrame, funding: pd.DataFrame) -> tuple[np.ndarray, list[str]]:
    base = compute_base_ta(ohlcv)
    mtf = compute_mtf_features(ohlcv, ("4h", "1d"))
    fund_feat = compute_funding_features(ohlcv, funding)
    feature_names = BASE_FEATURES + list(mtf.columns) + FUNDING_FEATURES
    combined = pd.concat([base, mtf, fund_feat], axis=1)
    combined = combined.bfill().ffill().fillna(0.0).astype(np.float64)
    return combined.values, feature_names


def build_X_row(ohlcv_row: pd.DataFrame, ohlcv_hist: pd.DataFrame,
                funding_row: float, funding_hist: pd.DataFrame) -> np.ndarray:
    """Build a single feature row using historical data up to this bar."""
    X, _ = build_X(ohlcv_hist, funding_hist)
    return X[-1:]


def calc_size(free_balance: float) -> float:
    return min(free_balance * 0.33, free_balance)


def run_backtest_with_model(model, scaler, feature_names: list[str],
                            ohlcv_test: pd.DataFrame, funding_test: pd.DataFrame) -> dict:
    """Run event-driven backtest on test period using pre-loaded model."""
    t_start = time.time()

    ohlcv = ohlcv_test.reset_index(drop=True)
    funding = funding_test.reset_index(drop=True)

    # Pre-compute features for test period
    logger.info("  Computing features for test period...")
    t0 = time.time()
    X_test, fn = build_X(ohlcv, funding)
    logger.info(f"  Features: {X_test.shape[0]} × {X_test.shape[1]}  [{time.time() - t0:.1f}s]")
    X_scaled = scaler.transform(X_test)
    y_proba = model.predict_proba(X_scaled)[:, 1]
    logger.info(f"  Inference: {len(y_proba)} samples  [{time.time() - t0:.1f}s]")

    # Generate signals
    signals = np.full(len(y_proba), 0, dtype=int)
    signals[y_proba > BUY_THRESHOLD] = 1
    signals[y_proba < SELL_THRESHOLD] = -1
    n_buy = int((signals == 1).sum())
    n_sell = int((signals == -1).sum())
    n_hold = int((signals == 0).sum())
    logger.info(f"  Signals: {n_buy} BUY / {n_sell} SELL / {n_hold} HOLD")

    close = ohlcv["close"].values.astype(float)

    # State
    pos: Position | None = None
    free_balance = INITIAL_CAPITAL
    trades: list[TradeRecord] = []
    equity_log: list[dict] = []
    last_log = time.time()

    # Bar loop
    n_bars = len(ohlcv)
    for bar_idx in range(n_bars):
        if bar_idx % 2000 == 0:
            pct = bar_idx / n_bars * 100
            elapsed = time.time() - t_start
            logger.info(f"    [{pct:.0f}%] bar={bar_idx}/{n_bars}  "
                        f"trades={len(trades)}  balance=${free_balance:,.0f}  "
                        f"elapsed={elapsed:.0f}s")

        current_close = close[bar_idx]
        signal = signals[bar_idx]
        bar_ts = ohlcv["timestamp"].iloc[bar_idx]

        # Flip on opposite signal
        if pos is not None and signal != 0 and signal != pos.side:
            gross_return = current_close / pos.entry_price - 1.0
            gross_pnl = pos.size * (gross_return if pos.side == 1 else -gross_return)
            exit_cost = pos.size * COST_PER_SIDE
            net_pnl = gross_pnl - exit_cost
            free_balance += pos.size + pos.entry_cost + net_pnl

            trades.append(TradeRecord(
                entry_bar=pos.entry_bar,
                entry_ts=str(ohlcv.iloc[pos.entry_bar]["timestamp"]),
                exit_bar=bar_idx,
                exit_ts=str(bar_ts),
                side=pos.side,
                entry_price=pos.entry_price,
                exit_price=current_close,
                size=pos.size,
                gross_pnl=round(gross_pnl, 2),
                entry_cost=pos.entry_cost,
                exit_cost=round(exit_cost, 4),
                net_pnl=round(net_pnl, 2),
                duration_bars=bar_idx - pos.entry_bar,
                exit_reason="signal_flip",
            ))
            pos = None

        # Open new position
        if signal != 0 and pos is None:
            entry_price = current_close
            size = calc_size(free_balance)
            entry_cost = size * COST_PER_SIDE
            if size > 1.0:
                pos = Position(
                    side=signal, entry_bar=bar_idx,
                    entry_price=entry_price, size=size,
                    entry_cost=entry_cost,
                )
                free_balance -= (size + entry_cost)

        # Close after 5 bars
        elif pos is not None and (bar_idx - pos.entry_bar) >= LOOKAHEAD:
            if signal == 0:
                gross_return = current_close / pos.entry_price - 1.0
                gross_pnl = pos.size * (gross_return if pos.side == 1 else -gross_return)
                exit_cost = pos.size * COST_PER_SIDE
                net_pnl = gross_pnl - exit_cost
                free_balance += pos.size + pos.entry_cost + net_pnl

                trades.append(TradeRecord(
                    entry_bar=pos.entry_bar,
                    entry_ts=str(ohlcv.iloc[pos.entry_bar]["timestamp"]),
                    exit_bar=bar_idx,
                    exit_ts=str(bar_ts),
                    side=pos.side,
                    entry_price=pos.entry_price,
                    exit_price=current_close,
                    size=pos.size,
                    gross_pnl=round(gross_pnl, 2),
                    entry_cost=pos.entry_cost,
                    exit_cost=round(exit_cost, 4),
                    net_pnl=round(net_pnl, 2),
                    duration_bars=bar_idx - pos.entry_bar,
                    exit_reason="signal",
                ))
                pos = None

        # MTM equity
        unrealized = 0.0
        if pos is not None:
            mtm_return = current_close / pos.entry_price - 1.0
            mtm_pnl = pos.size * (mtm_return if pos.side == 1 else -mtm_return)
            unrealized += mtm_pnl

        equity_log.append({
            "timestamp": str(bar_ts),
            "bar": int(bar_idx),
            "equity": round(free_balance + unrealized, 2),
            "free_balance": round(free_balance, 2),
            "unrealized_pnl": round(unrealized, 2),
            "open_positions": 1 if pos is not None else 0,
        })

    elapsed = time.time() - t_start
    logger.info(f"  [backtest] complete: {elapsed:.0f}s  trades={len(trades)}  "
                f"final_equity=${free_balance:,.0f}")

    # ── Metrics ──────────────────────────────────────────────────
    if len(trades) == 0:
        return {"status": "no_trades"}

    df_trades = pd.DataFrame([{
        "entry_ts": t.entry_ts, "exit_ts": t.exit_ts,
        "side": "LONG" if t.side == 1 else "SHORT",
        "size": t.size, "gross_pnl": t.gross_pnl,
        "entry_cost": t.entry_cost, "exit_cost": t.exit_cost,
        "net_pnl": t.net_pnl, "duration_bars": t.duration_bars,
        "exit_reason": t.exit_reason,
    } for t in trades])

    eq_df = pd.DataFrame(equity_log)
    eq_df["timestamp"] = pd.to_datetime(eq_df["timestamp"])
    eq_df = eq_df.set_index("timestamp").sort_index()
    daily_eq = eq_df["equity"].resample("D").last().dropna()
    daily_ret = daily_eq.pct_change().dropna()

    if len(daily_ret) > 5:
        mean_d = daily_ret.mean()
        std_d = daily_ret.std()
        sharpe = mean_d / std_d * np.sqrt(365) if std_d > 1e-10 else 0.0
        downside = daily_ret[daily_ret < 0]
        downside_std = downside.std() if len(downside) > 1 else 0.0
        sortino = mean_d / downside_std * np.sqrt(365) if downside_std > 1e-10 else 0.0
    else:
        sharpe = 0.0
        sortino = 0.0

    equity = daily_eq.values
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / peak
    max_dd = float(dd.min())

    n_days = len(daily_eq)
    years = n_days / 365.0
    total_return = float(equity[-1] / equity[0] - 1.0) if equity[0] > 0 else 0.0
    cagr = (equity[-1] / equity[0]) ** (1.0 / years) - 1.0 if years > 0 and equity[0] > 0 else 0.0
    calmar = cagr / abs(max_dd) if max_dd < 0 else 0.0

    wins = df_trades[df_trades["net_pnl"] > 0]
    losses = df_trades[df_trades["net_pnl"] <= 0]
    win_rate = len(wins) / len(df_trades) if len(df_trades) > 0 else 0.0
    profit_factor = abs(wins["net_pnl"].sum()) / abs(losses["net_pnl"].sum() + 1e-10)
    avg_dur = df_trades["duration_bars"].mean()
    total_costs = df_trades["entry_cost"].sum() + df_trades["exit_cost"].sum()

    n_bars_with_positions = sum(1 for e in equity_log if e["open_positions"] > 0)
    exposure_pct = n_bars_with_positions / len(equity_log) * 100

    dd_squared = (dd ** 2).mean()
    ulcer = np.sqrt(dd_squared) if dd_squared > 0 else 0.0
    total_gain = float(equity[-1] - equity[0])
    max_dd_abs = float(np.max(np.abs(dd)) * (equity[0] if len(equity) > 0 else 1))
    recovery_factor = total_gain / max_dd_abs if max_dd_abs > 1e-10 else 0.0
    underwater = equity < peak
    tuw_pct = underwater.mean() * 100

    metrics = {
        "n_trades": len(trades),
        "n_wins": int(len(wins)),
        "n_losses": int(len(losses)),
        "win_rate": round(win_rate, 4),
        "profit_factor": round(profit_factor, 4),
        "total_return": round(total_return, 6),
        "cagr": round(cagr, 4),
        "sharpe": round(sharpe, 4),
        "sortino": round(sortino, 4),
        "calmar": round(calmar, 4),
        "max_drawdown": round(max_dd, 6),
        "ulcer_index": round(float(ulcer), 6),
        "recovery_factor": round(recovery_factor, 4),
        "time_under_water_pct": round(float(tuw_pct), 2),
        "avg_duration_bars": round(float(avg_dur), 1),
        "exposure_pct": round(float(exposure_pct), 2),
        "total_costs": round(float(total_costs), 2),
        "final_equity": round(float(equity[-1]) if len(equity) > 0 else free_balance, 2),
        "peak_equity": round(float(peak[-1]) if len(peak) > 0 else free_balance, 2),
        "elapsed_s": round(elapsed, 1),
    }

    df_trades.to_parquet(REPORT_DIR / "backtest_trade_log.parquet", index=False)
    pd.DataFrame(equity_log).to_csv(REPORT_DIR / "backtest_equity_curve.csv", index=False)

    return metrics


def main():
    logger.info("╔════════════════════════════════════════════════════════════╗")
    logger.info("║  Phase 15b — Backtest Validation (.pkl)                  ║")
    logger.info("║  Train: 2023-06-01 → 2024-12-31  (18mo)                  ║")
    logger.info("║  Test:  2025-01-01 → 2026-06-16  (18mo)                  ║")
    logger.info("║  Model: LogisticRegression(C=0.1, balanced)              ║")
    logger.info("╚════════════════════════════════════════════════════════════╝")
    overall_start = time.time()

    # ── 1. Load data ──────────────────────────────────────────────
    logger.info("")
    logger.info("─" * 56)
    logger.info("▸ Loading data...")
    ohlcv = load_ohlcv(SYMBOL)
    funding = load_funding(SYMBOL)

    logger.info(f"  {SYMBOL} OHLCV: {len(ohlcv)} rows ({ohlcv['timestamp'].min()} → {ohlcv['timestamp'].max()})")
    logger.info(f"  {SYMBOL} Funding: {len(funding)} rows")

    # ── 2. Split ──────────────────────────────────────────────────
    train_mask = (ohlcv["timestamp"] >= TRAIN_START) & (ohlcv["timestamp"] <= TRAIN_END)
    test_mask = (ohlcv["timestamp"] >= TEST_START) & (ohlcv["timestamp"] <= TEST_END)
    ohlcv_train = ohlcv[train_mask].copy()
    ohlcv_test = ohlcv[test_mask].copy()
    fund_train = funding[(funding["timestamp"] >= TRAIN_START) & (funding["timestamp"] <= TRAIN_END)].copy()
    fund_test = funding[(funding["timestamp"] >= TEST_START) & (funding["timestamp"] <= TEST_END)].copy()

    logger.info(f"  Train: {len(ohlcv_train)} rows  Test: {len(ohlcv_test)} rows")

    # ── 3. Train features + labels ────────────────────────────────
    logger.info("▸ Training model on 2023-2024 data...")
    t0 = time.time()
    X_train, feature_names = build_X(ohlcv_train, fund_train)
    y_train, mask = label_binary(ohlcv_train, lookahead=LOOKAHEAD, threshold=0.5)
    valid = np.where(mask)[0]
    valid = valid[valid >= LOOKAHEAD + 1]
    valid = valid[::5]
    X_train_emb = X_train[valid]
    y_train_emb = y_train[valid]
    logger.info(f"  Features: {X_train_emb.shape[0]} train samples [{time.time() - t0:.1f}s]")

    scaler = RobustScaler()
    X_scaled = scaler.fit_transform(X_train_emb)
    model = LogisticRegression(**MODEL_PARAMS)
    model.fit(X_scaled, y_train_emb)
    train_acc = accuracy_score(y_train_emb, model.predict(X_scaled))
    train_f1 = f1_score(y_train_emb, model.predict(X_scaled), zero_division=0)
    logger.info(f"  Train Acc: {train_acc:.4f}  F1: {train_f1:.4f}")

    # ── 4. Serialize to temp .pkl ─────────────────────────────────
    logger.info("▸ Serializing model to temp .pkl...")
    tmp_dir = Path("/tmp/argos_bt_validate")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, tmp_dir / "model.pkl")
    joblib.dump(scaler, tmp_dir / "scaler.pkl")
    logger.info(f"  Saved to {tmp_dir}/")

    # ── 5. Load back from .pkl ────────────────────────────────────
    logger.info("▸ Loading model from .pkl (roundtrip check)...")
    model2 = joblib.load(tmp_dir / "model.pkl")
    scaler2 = joblib.load(tmp_dir / "scaler.pkl")

    y_before = model.predict(X_scaled)
    X2 = scaler2.transform(X_train_emb)
    y_after = model2.predict(X2)
    n_match = int((y_after == y_before).sum())
    logger.info(f"  Roundtrip: {n_match}/{len(y_before)} predictions match "
                f"{'✓' if n_match == len(y_before) else '✗'}")

    # ── 6. Backtest on test period ────────────────────────────────
    logger.info("")
    logger.info("─" * 56)
    logger.info(f"▸ Running backtest on {TEST_START} → {TEST_END}...")
    bt_results = run_backtest_with_model(model2, scaler2, feature_names, ohlcv_test, fund_test)

    # ── 7. Phase 11 comparison ────────────────────────────────────
    logger.info("")
    logger.info("─" * 56)
    logger.info("▸ Phase 11 comparison...")
    phase11_path = Path(__file__).parent.parent.parent / "reports" / "quant_validation_v2_phase11" / "phase11_results.json"
    if phase11_path.exists():
        with open(phase11_path) as f:
            p11 = json.load(f)
        logger.info(f"  Phase 11 (all-time):")
        logger.info(f"    Trades: {p11.get('n_trades', '?')}  Sharpe: {p11.get('sharpe', '?'):.2f}  "
                    f"MaxDD: {p11.get('max_drawdown', '?'):.2%}  WinRate: {p11.get('win_rate', '?'):.1%}")
    else:
        p11 = {}
        logger.info("  Phase 11 results not found (no comparison possible)")

    # ── 8. Report ─────────────────────────────────────────────────
    logger.info("")
    logger.info("═" * 56)
    logger.info("  BACKTEST VALIDATION RESULTS — BTC")
    logger.info("═" * 56)
    logger.info(f"  {'Metric':25s} {'This (15b .pkl)':20s} {'Phase 11 (ref)':20s}")
    logger.info(f"  {'------':25s} {'---------------':20s} {'---------------':20s}")

    def fmt(v, default="—"):
        if v is None or v == "—":
            return default
        if isinstance(v, float):
            if abs(v) < 1:
                return f"{v:.4f}"
            return f"{v:.2f}"
        return str(v)

    rows = [
        ("Trades", bt_results.get("n_trades"), p11.get("n_trades")),
        ("Sharpe", bt_results.get("sharpe"), p11.get("sharpe")),
        ("Sortino", bt_results.get("sortino"), p11.get("sortino")),
        ("CAGR", bt_results.get("cagr"), p11.get("cagr")),
        ("Max Drawdown", bt_results.get("max_drawdown"), p11.get("max_drawdown")),
        ("Win Rate", bt_results.get("win_rate"), p11.get("win_rate")),
        ("Profit Factor", bt_results.get("profit_factor"), p11.get("profit_factor")),
        ("Total Return", bt_results.get("total_return"), p11.get("total_return")),
        ("Calmar", bt_results.get("calmar"), p11.get("calmar")),
        ("Recovery Factor", bt_results.get("recovery_factor"), p11.get("recovery_factor")),
        ("Avg Duration (bars)", bt_results.get("avg_duration_bars"), p11.get("avg_duration_bars")),
        ("Exposure %", bt_results.get("exposure_pct"), p11.get("exposure_pct")),
        ("Total Costs", bt_results.get("total_costs"), p11.get("total_costs")),
        ("Final Equity", bt_results.get("final_equity"), p11.get("final_equity")),
    ]
    for name, v1, v2 in rows:
        logger.info(f"  {name:25s} {fmt(v1):>20s} {fmt(v2):>20s}")

    logger.info("")
    logger.info(f"  Train period: {TRAIN_START} → {TRAIN_END}")
    logger.info(f"  Test period:  {TEST_START} → {TEST_END}")
    logger.info(f"  Roundtrip:    {'PASS ✓' if n_match == len(y_before) else 'FAIL ✗'}")
    logger.info(f"  Elapsed:      {time.time() - overall_start:.0f}s")

    # ── Save report ───────────────────────────────────────────────
    report = {
        "phase": "quant_validation_v2_phase15_backtest",
        "symbol": SYMBOL,
        "train_period": [TRAIN_START, TRAIN_END],
        "test_period": [TEST_START, TEST_END],
        "model_params": MODEL_PARAMS,
        "roundtrip_ok": n_match == len(y_before),
        "train_metrics": {"accuracy": round(train_acc, 4), "f1": round(train_f1, 4)},
        "backtest_metrics": bt_results,
        "phase11_reference": p11 if p11 else None,
        "elapsed_s": round(time.time() - overall_start, 1),
    }
    report_path = REPORT_DIR / "backtest_validation.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    logger.info(f"  Report: {report_path}")
    logger.info("═" * 56)

    tmp_model = tmp_dir / "model.pkl"
    tmp_scaler = tmp_dir / "scaler.pkl"
    tmp_model.unlink(missing_ok=True)
    tmp_scaler.unlink(missing_ok=True)
    tmp_dir.rmdir() if tmp_dir.exists() else None


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error(f"FAILED: {e}")
        logger.error(traceback.format_exc())
