"""Phase 7 — Economic Alpha Backtest.

Applies threshold-based position sizing (BUY>0.60, SELL<0.40) and
realistic costs (0.31% round-trip) to Phase 5 predictions.

Reports economic metrics CAGR, Sharpe, Sortino, Calmar, Max DD, Win Rate, Profit Factor.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger("phase7")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

PHASE5_REPORT_DIR = Path(__file__).parent.parent.parent / "reports" / "quant_validation_v2_phase5"
REPORT_DIR = Path(__file__).parent.parent.parent / "reports" / "quant_validation_v2_phase7"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

SYMBOLS = ["BTC", "ETH", "SOL"]
COST_PER_SIDE = 0.00155  # fee 0.0010 + slippage 0.0005 + spread/2 0.00005
COST_ROUND_TRIP = COST_PER_SIDE * 2  # 0.0031 = 0.31%
BUY_THRESHOLD = 0.60
SELL_THRESHOLD = 0.40


def load_predictions_parquet(filename: str) -> pd.DataFrame | None:
    path = PHASE5_REPORT_DIR / filename
    if not path.exists():
        logger.warning(f"[parquet] not found: {path}")
        return None
    df = pd.read_parquet(path)
    logger.info(f"[parquet] loaded {len(df)} predictions from {path}")
    return df


def save_report(data: dict, filename: str) -> Path:
    path = REPORT_DIR / filename
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    logger.info(f"[report] saved {path}")
    return path


# ── position sizing with thresholds ──────────────────────────────


def apply_thresholds(
    df: pd.DataFrame,
    buy_threshold: float = BUY_THRESHOLD,
    sell_threshold: float = SELL_THRESHOLD,
) -> pd.DataFrame:
    """Add 'position' column: 1 = BUY, -1 = SELL, 0 = HOLD."""
    pos = np.zeros(len(df), dtype=np.int8)
    pos[df["y_proba"].values > buy_threshold] = 1
    pos[df["y_proba"].values < sell_threshold] = -1
    df = df.copy()
    df["position"] = pos
    return df


def compute_trade_returns(df: pd.DataFrame) -> pd.Series:
    """Compute per-trade P&L with costs.

    BUY (pos=1):  ret = forward_return - cost
    SELL (pos=-1): ret = -forward_return - cost
    HOLD (pos=0): ret = 0.0 (no trade, no cost)
    """
    mask_long = df["position"] == 1
    mask_short = df["position"] == -1
    ret = np.zeros(len(df))
    ret[mask_long] = df.loc[mask_long, "forward_return"].values - COST_ROUND_TRIP
    ret[mask_short] = -df.loc[mask_short, "forward_return"].values - COST_ROUND_TRIP
    return pd.Series(ret, index=pd.to_datetime(df["timestamp"])).sort_index()


def compute_portfolio_metrics(
    trade_returns: pd.Series,
    name: str = "portfolio",
    annual_factor: float = 365.0,
) -> dict:
    if len(trade_returns) < 10:
        return {"name": name, "status": "insufficient_data", "n_trades": len(trade_returns)}

    daily = trade_returns.resample("D").sum().dropna()
    if len(daily) < 5:
        return {"name": name, "status": "insufficient_data", "n_trades": len(trade_returns), "n_days": len(daily)}

    equity = (1.0 + daily).cumprod()
    total_return = float(equity.iloc[-1] / equity.iloc[0] - 1.0)
    n_days = len(daily)
    years = n_days / annual_factor

    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1.0 / years) - 1.0 if years > 0 and equity.iloc[0] > 0 else 0.0

    mean_daily = daily.mean()
    std_daily = daily.std()
    sharpe = (mean_daily / std_daily * np.sqrt(annual_factor)) if std_daily > 1e-10 else 0.0

    downside = daily[daily < 0]
    downside_std = downside.std() if len(downside) > 1 else 0.0
    sortino = (mean_daily / downside_std * np.sqrt(annual_factor)) if downside_std > 1e-10 else 0.0

    peak = equity.cummax()
    dd = (equity - peak) / peak
    max_dd = float(dd.min())
    calmar = cagr / abs(max_dd) if max_dd < 0 else 0.0

    wins = (trade_returns > 0).sum()
    total = len(trade_returns)
    win_rate = wins / total if total > 0 else 0.0

    gains = trade_returns[trade_returns > 0].sum()
    losses = trade_returns[trade_returns < 0].sum()
    profit_factor = float(gains / abs(losses)) if abs(losses) > 1e-10 else 0.0

    return {
        "name": name,
        "n_trades": int(total),
        "n_days": int(n_days),
        "n_hodl": len(trade_returns) - total,
        "total_return": round(total_return, 6),
        "cagr": round(cagr, 6),
        "sharpe": round(sharpe, 4),
        "sortino": round(sortino, 4),
        "calmar": round(calmar, 4),
        "max_dd": round(max_dd, 6),
        "win_rate": round(win_rate, 4),
        "profit_factor": round(profit_factor, 4),
        "mean_daily_return": round(float(mean_daily), 8),
        "std_daily_return": round(float(std_daily), 8),
    }


# ── main ─────────────────────────────────────────────────────────


def analyze_symbol(symbol: str) -> dict | None:
    df = load_predictions_parquet(f"{symbol.lower()}_predictions.parquet")
    if df is None or len(df) < 100:
        return None

    df = apply_thresholds(df, BUY_THRESHOLD, SELL_THRESHOLD)
    n_buy = int((df["position"] == 1).sum())
    n_sell = int((df["position"] == -1).sum())
    n_hodl = int((df["position"] == 0).sum())
    n_total = len(df)

    # Only trades with position != 0
    traded = df[df["position"] != 0]
    if len(traded) == 0:
        logger.warning(f"[{symbol}] no trades after thresholds")
        return {
            "symbol": symbol,
            "thresholds": {"buy": BUY_THRESHOLD, "sell": SELL_THRESHOLD},
            "cost_round_trip": COST_ROUND_TRIP,
            "n_total_predictions": n_total,
            "n_buy": n_buy,
            "n_sell": n_sell,
            "n_hodl": n_hodl,
            "n_trades": 0,
            "status": "no_trades",
        }

    trade_returns = compute_trade_returns(traded)
    metrics = compute_portfolio_metrics(trade_returns, name=symbol)

    # Also compute no-cost comparison
    trade_returns_no_cost_p = np.where(
        traded["position"] == 1,
        traded["forward_return"].values,
        -traded["forward_return"].values,
    )
    trade_returns_no_cost_series = pd.Series(
        trade_returns_no_cost_p,
        index=pd.to_datetime(traded["timestamp"]),
    ).sort_index()
    nc_metrics = compute_portfolio_metrics(trade_returns_no_cost_series, name=f"{symbol}_no_cost")

    result = {
        "symbol": symbol,
        "thresholds": {"buy": BUY_THRESHOLD, "sell": SELL_THRESHOLD},
        "cost_round_trip": COST_ROUND_TRIP,
        "n_total_predictions": n_total,
        "n_buy": int(n_buy),
        "n_sell": int(n_sell),
        "n_hodl": int(n_hodl),
        "trade_rate_pct": round((n_buy + n_sell) / n_total * 100, 2),
        "with_costs": metrics,
        "without_costs": nc_metrics,
    }

    logger.info(
        f"[{symbol}] trades={metrics.get('n_trades', 0)}/{n_total} "
        f"({result['trade_rate_pct']:.0f}%)  "
        f"Sharpe={metrics.get('sharpe', 0):.2f}  "
        f"CAGR={metrics.get('cagr', 0):.4%}  "
        f"MaxDD={metrics.get('max_dd', 0):.4%}"
    )
    return result


def main():
    logger.info("╔══════════════════════════════════════╗")
    logger.info("║  Phase 7 — Economic Alpha Backtest    ║")
    logger.info("║  Thresholds >0.60 / <0.40            ║")
    logger.info("║  Cost round-trip: 0.31%              ║")
    logger.info("╚══════════════════════════════════════╝")

    per_symbol = {}
    for sym in SYMBOLS:
        r = analyze_symbol(sym)
        per_symbol[sym] = r or {"status": "error"}

    # ── Portfolio with costs ──────────────────────────────────────
    all_trade_returns = []
    for sym in SYMBOLS:
        df = apply_thresholds(load_predictions_parquet(f"{sym.lower()}_predictions.parquet"))
        if df is None:
            continue
        traded = df[df["position"] != 0]
        if len(traded) < 10:
            continue
        ret = compute_trade_returns(traded)
        ret.name = sym
        all_trade_returns.append(ret)

    if all_trade_returns:
        portfolio_df = pd.DataFrame(all_trade_returns).T
        portfolio_df["portfolio"] = portfolio_df.mean(axis=1)
        port_metrics = compute_portfolio_metrics(portfolio_df["portfolio"], name="equal_weight_portfolio")
        logger.info(
            f"[PORTFOLIO] Sharpe={port_metrics.get('sharpe'):.2f}  "
            f"CAGR={port_metrics.get('cagr'):.4%}  "
            f"MaxDD={port_metrics.get('max_dd'):.4%}"
        )

        # Without costs for comparison
        no_cost_returns = []
        for sym in SYMBOLS:
            df = load_predictions_parquet(f"{sym.lower()}_predictions.parquet")
            if df is None:
                continue
            df = apply_thresholds(df)
            traded = df[df["position"] != 0]
            if len(traded) < 10:
                continue
            ret_nc = pd.Series(
                np.where(traded["position"] == 1, traded["forward_return"].values, -traded["forward_return"].values),
                index=pd.to_datetime(traded["timestamp"]),
            ).sort_index()
            ret_nc.name = sym
            no_cost_returns.append(ret_nc)

        if no_cost_returns:
            nc_df = pd.DataFrame(no_cost_returns).T
            nc_df["portfolio"] = nc_df.mean(axis=1)
            port_nc = compute_portfolio_metrics(nc_df["portfolio"], name="no_cost_portfolio")
        else:
            port_nc = {"status": "no_data"}
    else:
        port_metrics = {"status": "no_data"}
        port_nc = {"status": "no_data"}

    # ── Verdict ──────────────────────────────────────────────────
    verdict = None
    if port_metrics.get("status") != "no_data":
        sp = port_metrics.get("sharpe", 0)
        if sp > 1.0:
            verdict = "ALPHA SURVIVES"
        elif sp > 0:
            verdict = "ALPHA ERODED"
        else:
            verdict = "ALPHA DESTROYED"

    output = {
        "phase": "quant_validation_v2_phase7",
        "thresholds": {"buy": BUY_THRESHOLD, "sell": SELL_THRESHOLD},
        "cost_round_trip": COST_ROUND_TRIP,
        "per_symbol": per_symbol,
        "portfolio_with_costs": port_metrics,
        "portfolio_without_costs": port_nc,
        "verdict": verdict,
    }
    save_report(output, "economic_alpha.json")
    logger.info(f"\nPhase 7 complete — verdict: {verdict}")


if __name__ == "__main__":
    main()
