"""QV2 Phase 3.5 Summary.

Loads PRIMARY (5,5) results, applies gates, emits verdict.
Sanity checks (1,1) and (3,3) are diagnostic only — NOT for decision.
"""

from __future__ import annotations

import json
from pathlib import Path

REPORT_DIR = Path(__file__).parent.parent.parent / "reports" / "quant_validation_v2_phase35"
QV2_REPORT_DIR = Path(__file__).parent.parent.parent / "reports" / "quant_validation_v2"


def load_json(path: Path) -> dict:
    if not path.exists():
        return {"error": f"not found: {path}"}
    with open(path) as f:
        return json.load(f)


def phase_val(phase: dict, metric: str) -> float:
    """Extract best model metric from a phase result."""
    models = phase.get("models", {})
    if metric == "best_f1":
        vals = [m.get("avg_f1", 0) for m in models.values()]
    elif metric == "best_r2":
        vals = [m.get("avg_r2", 0) for m in models.values()]
    elif metric == "best_auc":
        vals = [m.get("avg_auc", 0) for m in models.values() if m.get("avg_auc") is not None]
    else:
        return 0.0
    return max(vals) if vals else 0.0


def phase_sd(phase: dict, metric: str) -> float:
    """Extract best shuffle delta from a phase result."""
    models = phase.get("models", {})
    if metric == "f1":
        deltas = [m.get("shuffle_delta", -999) for m in models.values()]
    elif metric == "r2":
        deltas = [m.get("shuffle_r2_delta", -999) for m in models.values()]
    else:
        return 0.0
    return max(deltas) if deltas else 0.0


def best_null(phase: dict, metric: str, is_r2: bool = False) -> float:
    nulls = phase.get("null_baselines", {})
    if isinstance(nulls, list):
        if is_r2:
            vals = [n.get("r2", -999) for n in nulls]
        else:
            vals = [n.get("f1_macro", 0) for n in nulls]
    else:
        if is_r2:
            vals = [n.get("r2", -999) for n in nulls.values()]
        else:
            vals = [n.get("f1_macro", 0) for n in nulls.values()]
    return max(vals) if vals else -999


def print_phase_table(rows: list[list[str]]):
    """Pretty-print a comparison table."""
    col_widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    sep = "  "
    for row in rows:
        print(sep.join(cell.ljust(w) for cell, w in zip(row, col_widths)))


def main() -> dict:
    print("=" * 65)
    print("QV2 Phase 3.5 — Non-overlapping Labels (PRIMARY(5,5))")
    print("=" * 65)

    primary = load_json(REPORT_DIR / "phase35_nonoverlap.json")
    if "error" in primary:
        print(f"ERROR: {primary['error']}")
        return {"error": primary["error"]}

    sanity_1 = load_json(REPORT_DIR / "horizon_1.json")
    sanity_3 = load_json(REPORT_DIR / "horizon_3.json")
    qv2_phases = {
        "3A_multiclass": load_json(QV2_REPORT_DIR / "phase3a_multiclass.json"),
        "3B_binary": load_json(QV2_REPORT_DIR / "phase3b_binary.json"),
        "3C_regression": load_json(QV2_REPORT_DIR / "phase3c_regression.json"),
    }

    p_phases = primary.get("phases", {})
    s1_phases = sanity_1.get("phases", {})
    s3_phases = sanity_3.get("phases", {})

    n_orig = primary.get("n_samples_original", "?")
    n_sub = primary.get("n_samples_subsampled", "?")
    print(f"\nData: {n_orig} OHLCV bars → {n_sub} non-overlap samples (stride=5, embargo=1)")

    # ── Results table ─────────────────────────────────────────────

    print(f"\n{'Phase':20s} {'Metric':10s} {'QV2':>8s} {'P3.5':>8s} {'Δ':>8s} {'Pass?':>6s}")
    print("-" * 65)

    # Phase 3A Multiclass
    p3a = p_phases.get("3A_multiclass", {})
    q3a = qv2_phases.get("3A_multiclass", {})
    f1_qv2 = phase_val(q3a, "best_f1") if q3a else 0
    f1_p35 = phase_val(p3a, "best_f1")
    sd_qv2 = phase_sd(q3a, "f1") if q3a else 0
    sd_p35 = phase_sd(p3a, "f1")
    null_qv2 = best_null(q3a, "f1") if q3a else 0
    null_p35 = best_null(p3a, "f1")
    beats_qv2 = f1_p35 > null_qv2 * 1.01 if null_qv2 else False
    beats_p35 = f1_p35 > null_p35 * 1.01 if null_p35 else False

    print(f"{'3A Multiclass':20s} {'f1':10s} {f1_qv2:>8.4f} {f1_p35:>8.4f} {f1_p35 - f1_qv2:>+8.4f} {'':>6s}")
    print(f"{'':20s} {'shuffle_delta':10s} {sd_qv2:>8.4f} {sd_p35:>8.4f} {sd_p35 - sd_qv2:>+8.4f} {'':>6s}")
    b3a = "✅" if beats_p35 else "❌"
    print(f"{'':20s} {'beats_null':10s} {'':>8s} {'':>8s} {'':>8s} {b3a:>6s}")

    # Phase 3B Binary
    p3b = p_phases.get("3B_binary", {})
    q3b = qv2_phases.get("3B_binary", {})
    f1_qv2_b = phase_val(q3b, "best_f1") if q3b else 0
    f1_p35_b = phase_val(p3b, "best_f1")
    auc_p35 = phase_val(p3b, "best_auc")
    auc_qv2 = phase_val(q3b, "best_auc") if q3b else 0
    sd_qv2_b = phase_sd(q3b, "f1") if q3b else 0
    sd_p35_b = phase_sd(p3b, "f1")
    null_qv2_b = best_null(q3b, "f1") if q3b else 0
    null_p35_b = best_null(p3b, "f1")
    beats_p35_b = f1_p35_b > null_p35_b * 1.01 if null_p35_b else False

    print(f"{'3B Binary':20s} {'f1':10s} {f1_qv2_b:>8.4f} {f1_p35_b:>8.4f} {f1_p35_b - f1_qv2_b:>+8.4f} {'':>6s}")
    print(f"{'':20s} {'AUC':10s} {auc_qv2:>8.4f} {auc_p35:>8.4f} {auc_p35 - auc_qv2:>+8.4f} {'':>6s}")
    print(f"{'':20s} {'shuffle_delta':10s} {sd_qv2_b:>8.4f} {sd_p35_b:>8.4f} {sd_p35_b - sd_qv2_b:>+8.4f} {'':>6s}")
    b3b = "✅" if beats_p35_b else "❌"
    print(f"{'':20s} {'beats_null':10s} {'':>8s} {'':>8s} {'':>8s} {b3b:>6s}")

    # Phase 3C Regression
    p3c = p_phases.get("3C_regression", {})
    q3c = qv2_phases.get("3C_regression", {})
    r2_qv2 = phase_val(q3c, "best_r2") if q3c else 0
    r2_p35 = phase_val(p3c, "best_r2")
    sd_qv2_r2 = phase_sd(q3c, "r2") if q3c else 0
    sd_p35_r2 = phase_sd(p3c, "r2")
    null_qv2_r2 = best_null(q3c, "r2", True) if q3c else 0
    null_p35_r2 = best_null(p3c, "r2", True)
    beats_p35_r2 = r2_p35 > null_p35_r2 + 0.001

    print(f"{'3C Regression':20s} {'R²':10s} {r2_qv2:>8.4f} {r2_p35:>8.4f} {r2_p35 - r2_qv2:>+8.4f} {'':>6s}")
    print(f"{'':20s} {'shuffle_delta':10s} {sd_qv2_r2:>8.4f} {sd_p35_r2:>8.4f} {sd_p35_r2 - sd_qv2_r2:>+8.4f} {'':>6s}")
    b3c = "✅" if beats_p35_r2 else "❌"
    print(f"{'':20s} {'beats_null':10s} {'':>8s} {'':>8s} {'':>8s} {b3c:>6s}")

    # ── Sanity checks (diagnostic only) ──────────────────────────

    print(f"\n{'─' * 65}")
    print(f"Sanity checks (diagnostic, NOT for decision)")
    print(f"{'─' * 65}")

    for label, s_phases, s_n in [
        ("sanity (1,1)", s1_phases, sanity_1.get("n_samples_subsampled", 0)),
        ("sanity (3,3)", s3_phases, sanity_3.get("n_samples_subsampled", 0)),
    ]:
        print(f"\n{label:15s} n={s_n}")
        for pkey in ["3A_multiclass", "3B_binary", "3C_regression"]:
            sp = s_phases.get(pkey, {})
            if pkey == "3C_regression":
                val = phase_val(sp, "best_r2")
            else:
                val = phase_val(sp, "best_f1")
            sd = phase_sd(sp, "r2" if pkey == "3C_regression" else "f1")
            print(f"  {pkey:20s}  best={val:.4f}  shuffle_delta={sd:+.4f}")

    # ── GATES (PRIMARY only) ────────────────────────────────────

    print(f"\n{'=' * 65}")
    print("GATES — PRIMARY (5,5)")
    print("=" * 65)

    gate_binary_f1 = f1_p35_b > 0.60
    gate_binary_auc = auc_p35 > 0.70
    gate_regression_r2 = r2_p35 > 0.05
    gate_shuffle_positive = sd_p35 > 0.0 and sd_p35_b > 0.0 and sd_p35_r2 > 0.0
    gate_two_of_three = sum([
        f1_p35 > 0.55,     # multiclass advantage threshold
        gate_binary_f1,     # binary advantage
        gate_regression_r2, # regression advantage
    ]) >= 2

    gates = {
        "Binary F1 > 0.60": (gate_binary_f1, f1_p35_b),
        "Binary AUC > 0.70": (gate_binary_auc, auc_p35),
        "Regression R² > 0.05": (gate_regression_r2, r2_p35),
        "All shuffle deltas > 0": (gate_shuffle_positive, min(sd_p35, sd_p35_b, sd_p35_r2)),
        "At least 2/3 framings maintain advantage": (gate_two_of_three, None),
    }

    for desc, (passed, value) in gates.items():
        val_str = f"{value:.4f}" if value is not None else ""
        print(f"  {'✅' if passed else '❌'} {desc:45s} {val_str}")

    all_passed = all(p for p, _ in gates.values())
    print(f"\n  All gates passed: {'YES' if all_passed else 'NO'}")

    # ── VERDICT ──────────────────────────────────────────────

    print(f"\n{'=' * 65}")
    print("VERDICT")
    print("=" * 65)

    if all_passed:
        verdict = (
            "H1 SURVIVES — La señal observada en QV2 sobrevive a la "
            "eliminación de labels traslapados.\n\n"
            "Interpretación técnica:\n"
            "  - Al eliminar el solapamiento temporal (stride=5, embargo=1), "
            "los modelos mantienen\n"
            "    rendimiento por encima de baselines ingenua y aleatoria.\n"
            "  - La información proviene del feature space (53 features: "
            "TA+MTF+funding) y no\n"
            "    principalmente de la autocorrelación artificial de labels.\n"
            "  - Binary F1 > 0.60 y AUC > 0.70 confirman dirección explotable.\n"
            "  - Shuffle deltas positivos en los 3 framings descartan "
            "sobreajuste temporal.\n\n"
            "Próximo paso: cross-market validation (ETH, SOL, NASDAQ futures)."
        )
    else:
        verdict = (
            "H0 EXTENDED — La mejora observada en QV2 dependía "
            "principalmente del solapamiento\n"
            "temporal del target (labels traslapados con ~80% autocorrelación).\n\n"
            "Interpretación técnica:\n"
            "  - Al eliminar el solapamiento con stride=lookahead y embargo "
            "estricto, el rendimiento\n"
            "    de los modelos colapsa hacia baselines ingenuas.\n"
            "  - Las features MTF+Funding no contienen señal predictiva "
            "suficiente para generar\n"
            "    decisiones de trading independientes del label leakage.\n"
            "  - La línea de investigación con modelos simples + features "
            "técnicas se cierra aquí.\n\n"
            "Posibles direcciones futuras:\n"
            "  - Probabilistic forecasting (no supervisado sobre distribución "
            "de returns)\n"
            "  - Regime change detection (HMM, change point detection)\n"
            "  - Ordenes limitadas + market microstructure (no depender de "
            "predicción direccional)"
        )

    print(f"\n{verdict}")

    # Build summary report
    summary = {
        "experiment": "QV2_Phase35",
        "verdict": "H1_SURVIVES" if all_passed else "H0_EXTENDED",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "features": {"total": 53, "base_ta": 20, "mtf_4h": 15, "mtf_1d": 15, "funding": 3},
        "primary": {
            "lookahead": primary.get("lookahead", 5),
            "stride": primary.get("stride", 5),
            "embargo": primary.get("embargo", 1),
            "n_samples": primary.get("n_samples_subsampled", 0),
        },
        "gates": {desc: {"passed": p, "value": v} for desc, (p, v) in gates.items()},
        "all_gates_passed": all_passed,
        "results": {
            "multiclass_f1": round(f1_p35, 4),
            "binary_f1": round(f1_p35_b, 4),
            "binary_auc": round(auc_p35, 4),
            "regression_r2": round(r2_p35, 4),
            "multiclass_shuffle_delta": round(sd_p35, 4),
            "binary_shuffle_delta": round(sd_p35_b, 4),
            "regression_shuffle_delta": round(sd_p35_r2, 4),
        },
        "comparison_vs_qv2": {
            "multiclass_f1": {"qv2": round(f1_qv2, 4), "p35": round(f1_p35, 4), "delta": round(f1_p35 - f1_qv2, 4)},
            "binary_f1": {"qv2": round(f1_qv2_b, 4), "p35": round(f1_p35_b, 4), "delta": round(f1_p35_b - f1_qv2_b, 4)},
            "regression_r2": {"qv2": round(r2_qv2, 4), "p35": round(r2_p35, 4), "delta": round(r2_p35 - r2_qv2, 4)},
        },
        "verdict_text": verdict,
    }

    from .common import save_report as sr
    sr(summary, "summary.json")
    print(f"\nSummary saved to reports/quant_validation_v2_phase35/summary.json")
    return summary


if __name__ == "__main__":
    main()
