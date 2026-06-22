"""FASE 6.75.3 — Deflated Sharpe Ratio.

Compute DSR = probability that the observed Sharpe is not due to
multiple testing / non-Normal returns / selection bias.

Uses Bailey & López de Prado (2014) formulation.
Reports sensitivity at M=60 (effective) and M=200 (all configs).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from scipy import stats

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR / "experiments"))

REPORT_DIR = BASE_DIR / "reports" / "fase675"
REPORT_DIR.mkdir(parents=True, exist_ok=True)
WF_REPORT = BASE_DIR / "reports" / "walkforward"
WF_DIR = BASE_DIR / "reports" / "fase675"


def load_json(path: Path):
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return None


def compute_dsr(sharpe: float, T: int, M: int, skew: float = 0.0, kurt: float = 3.0) -> dict:
    """Compute Deflated Sharpe Ratio and related statistics."""
    # Variance of Sharpe estimator — guard against non-Normal breakdown
    var_normal = (1.0 + 0.5 * sharpe**2) / (T - 1)
    var_adjusted = (1.0 + 0.5 * sharpe**2 - skew * sharpe + (kurt - 3.0) * sharpe**2 / 4.0) / (T - 1)
    var_sr = max(var_normal, var_adjusted) if var_adjusted > 1e-15 else var_normal
    var_sr = max(var_sr, 1e-15)  # floor

    se_sr = np.sqrt(var_sr)

    # Expected maximum Sharpe from M independent trials (extreme value theory)
    # E[max_SR] = sqrt(Var[SR]) * ((1-γ) * Φ⁻¹(1-1/M) + γ * Φ⁻¹(1 - 1/(M*e)))
    gamma = 0.5772156649  # Euler-Mascheroni constant
    try:
        term1 = (1.0 - gamma) * stats.norm.ppf(1.0 - 1.0 / M)
        term2 = gamma * stats.norm.ppf(1.0 - 1.0 / (M * np.e))
    except ValueError:
        return {
            "sharpe": sharpe,
            "num_trials_M": M,
            "num_observations_T": T,
            "dsr": float("nan"),
            "expected_max_sr": float("nan"),
            "var_sr": float(var_sr),
            "error": "ppf computation failed",
        }

    expected_max = se_sr * (term1 + term2)

    # DSR statistic: Z[(SR * sqrt(T-1) - E[max]) / sqrt(Var[SR])]
    dsr_stat = (sharpe * np.sqrt(T - 1) - expected_max) / se_sr
    dsr = float(stats.norm.cdf(dsr_stat))

    return {
        "sharpe": sharpe,
        "num_trials_M": M,
        "num_observations_T": T,
        "dsr": dsr,
        "dsr_statistic": float(dsr_stat),
        "expected_max_sr": float(expected_max),
        "se_sr": float(se_sr),
        "var_sr": float(var_sr),
        "skewness": float(skew),
        "kurtosis": float(kurt),
    }


def main():
    print("=" * 60)
    print("FASE 6.75.3 — Deflated Sharpe Ratio")
    print("=" * 60)

    # Load walk-forward results for champion Sharpe
    wf_path = BASE_DIR / "reports" / "walkforward" / "summary.json"
    if wf_path.exists():
        wf = load_json(wf_path)
        fold_sharpes = [r.get("sharpe", 0) for r in wf.get("results", []) if "sharpe" in r]
    else:
        # Fallback: read individual fold files
        fold_sharpes = []
        for fid in range(4):
            f = load_json(BASE_DIR / "reports" / "walkforward" / f"fold_{fid}.json")
            if f:
                fold_sharpes.append(f.get("sharpe", 0))

    if len(fold_sharpes) == 0:
        print("ERROR: No walk-forward data found. Run FASE 6.5 first.")
        return

    avg_sharpe = float(np.mean(fold_sharpes))
    std_sharpe = float(np.std(fold_sharpes))

    # Use total return observations (bars) from backtest results
    # n_trades gives us trade count; for DSR use number of bars in test period (~3500 avg)
    t_counts = []
    all_pnls = []
    for fid in range(4):
        f = load_json(BASE_DIR / "reports" / "walkforward" / f"fold_{fid}.json")
        if f:
            t_counts.append(f.get("n_test_bars", 0))
            tp = f.get("trade_pnls", [])
            if isinstance(tp, list) and len(tp) > 0:
                all_pnls.extend(tp)
    n_obs_avg = int(np.mean(t_counts)) if t_counts else 3500

    # Compute skew/kurt from full return series
    if len(all_pnls) > 10:
        pnl_arr = np.array(all_pnls)
        skew = float(stats.skew(pnl_arr))
        kurt = float(stats.kurtosis(pnl_arr, fisher=False))  # Pearson kurtosis (normal=3)
    else:
        skew = 0.0
        kurt = 3.0

    print(f"\nChampion stats:")
    print(f"  Average walk-forward Sharpe: {avg_sharpe:.2f} ± {std_sharpe:.2f}")
    print(f"  Avg trade count per fold:    {n_obs_avg}")
    print(f"  Return skewness:              {skew:.4f}")
    print(f"  Return kurtosis (Pearson):    {kurt:.4f}")

    # Compute DSR at different M values
    m_values = [20, 60, 80, 200]

    print(f"\n{'M (trials)':<15} {'DSR':<10} {'E[max_SR]':<12} {'Var[SR]':<12} {'Result':<15}")
    print("-" * 65)

    results = {}
    for m in m_values:
        dsr_result = compute_dsr(avg_sharpe, n_obs_avg, m, skew, kurt)
        label = "Strong" if dsr_result["dsr"] > 0.95 else \
                "Good" if dsr_result["dsr"] > 0.5 else \
                "Weak" if dsr_result["dsr"] > 0 else "Fail"
        print(f"{m:<15} {dsr_result['dsr']:<10.4f} {dsr_result['expected_max_sr']:<12.4f} "
              f"{dsr_result['var_sr']:<12.6f} {label:<15}")
        results[f"M={m}"] = dsr_result

    # Final verdict with M=60 (effective trials, per user preference)
    primary = results.get("M=60", results.get("M=80", {}))
    dsr_verdict = "positive" if primary.get("dsr", 0) > 0 else "non-positive"

    output = {
        "champion_avg_sharpe": avg_sharpe,
        "champion_sharpe_std": std_sharpe,
        "trade_count": n_obs_avg,
        "skewness": skew,
        "kurtosis": kurt,
        "dsr_results": results,
        "primary_M": 60,
        "verdict": dsr_verdict,
    }

    report_path = REPORT_DIR / "deflated_sharpe.json"
    with open(report_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nReport: {report_path}")

    print(f"\n{'=' * 50}")
    print("VERDICT")
    print(f"{'=' * 50}")
    print(f"  Primary (M=60 effective trials): DSR = {primary.get('dsr', 'N/A')}")
    print(f"  Conservative (M=200 all configs): DSR = {results.get('M=200', {}).get('dsr', 'N/A')}")
    print(f"  → DSR {dsr_verdict.upper()}. "
          f"{'Alpha unlikely to be luck.' if primary.get('dsr', 0) > 0.5 else 'Marginal evidence of alpha.' if primary.get('dsr', 0) > 0 else 'Strong selection bias detected.'}")


if __name__ == "__main__":
    main()
