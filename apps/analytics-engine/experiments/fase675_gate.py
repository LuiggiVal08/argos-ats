"""FASE 6.75.6 — GATE 6.75 Evaluator.

Reads all 5 prior reports and produces a PASS/FAIL verdict.
See also: GATE 7 — Complexity Premium (applies once FASE 7 begins).
"""
from __future__ import annotations

import json
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
R675 = BASE_DIR / "reports" / "fase675"
WF_FILE = R675 / "purged_cv.json"
CPCV_FILE = R675 / "combinatorial_cv.json"
DSR_FILE = R675 / "deflated_sharpe.json"
PBO_FILE = R675 / "pbo.json"
SPA_FILE = R675 / "spa_test.json"

# Gate 6.5 reports for MC ruin
GATE65 = BASE_DIR / "reports" / "gate_6_5.json"
MC_DIR = BASE_DIR / "reports" / "montecarlo_fold"


def load_json(path: Path):
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return None


def evaluate():
    print("=" * 60)
    print("FASE 6.75.6 — GATE 6.75")
    print("=" * 60)
    print()
    print("Evaluating conditions for FASE 7 authorization...")
    print()

    conditions: dict[str, dict] = {}
    findings: list[str] = []

    # ── 1. Purged CV — Sharpe > 1.0 in ≥ 4/6 folds ──
    print("[1/8] Purged CV — Sharpe > 1.0 in ≥4/6 folds")
    purged = load_json(WF_FILE)
    if purged and "results" in purged:
        folds = purged["results"]
        sharpes = [f.get("sharpe", 0) for f in folds]
        calmars = [f.get("calmar", 0) for f in folds]
        n_pass_sharpe = sum(1 for s in sharpes if s > 1.5)
        n_pass_calmar = sum(1 for c in calmars if c > 2.0)
        mean_sharpe = float(sum(sharpes) / len(sharpes)) if sharpes else 0
        mean_calmar = float(sum(calmars) / len(calmars)) if calmars else 0

        cond_sharpe_fuerte = n_pass_sharpe >= 4
        cond_calmar_fuerte = n_pass_calmar >= 4
        cond_sharpe_debil = sum(1 for s in sharpes if s > 1.0) >= 4
        cond_calmar_debil = sum(1 for c in calmars if c > 1.0) >= 4

        conditions["purged_cv_sharpe"] = {
            "fuerte": cond_sharpe_fuerte, "debil": cond_sharpe_debil,
            "value": mean_sharpe, "n_pass": n_pass_sharpe, "n_total": len(folds),
        }
        conditions["purged_cv_calmar"] = {
            "fuerte": cond_calmar_fuerte, "debil": cond_calmar_debil,
            "value": mean_calmar, "n_pass": n_pass_calmar, "n_total": len(folds),
        }
        print(f"  Sharpe > 1.5 in {n_pass_sharpe}/{len(folds)} folds (mean={mean_sharpe:.2f}) "
              f"→ {'✓' if cond_sharpe_fuerte else '~' if cond_sharpe_debil else '✗'}")
        print(f"  Calmar > 2.0 in {n_pass_calmar}/{len(folds)} folds (mean={mean_calmar:.2f}) "
              f"→ {'✓' if cond_calmar_fuerte else '~' if cond_calmar_debil else '✗'}")
    else:
        conditions["purged_cv_sharpe"] = {"fuerte": False, "debil": False, "value": 0, "n_pass": 0, "n_total": 0}
        conditions["purged_cv_calmar"] = {"fuerte": False, "debil": False, "value": 0, "n_pass": 0, "n_total": 0}
        print("  NO DATA")

    # ── 2. CPCV stability ──
    print("\n[2/8] CPCV — Coefficient of Variation (CV) of Sharpe")
    cpcv = load_json(CPCV_FILE)
    if cpcv and "distribution" in cpcv:
        dist = cpcv["distribution"]
        sharpe_cv = dist.get("sharpe", {}).get("cv", float("inf"))
        cond_cv_fuerte = sharpe_cv < 0.5
        cond_cv_debil = sharpe_cv < 1.0
        conditions["cpcv_stability"] = {
            "fuerte": cond_cv_fuerte, "debil": cond_cv_debil,
            "cv_sharpe": sharpe_cv,
            "mean_sharpe": dist.get("sharpe", {}).get("mean", 0),
            "std_sharpe": dist.get("sharpe", {}).get("std", 0),
        }
        print(f"  Sharpe CV = {sharpe_cv:.2f} → "
              f"{'✓' if cond_cv_fuerte else '~' if cond_cv_debil else '✗'}")
    else:
        conditions["cpcv_stability"] = {"fuerte": False, "debil": False, "cv_sharpe": float("inf")}
        print("  NO DATA")

    # ── 3. DSR ──
    print("\n[3/8] Deflated Sharpe Ratio")
    dsr = load_json(DSR_FILE)
    if dsr and "dsr_results" in dsr:
        primary = dsr.get("dsr_results", {}).get("M=60", {})
        dsr_val = primary.get("dsr", -1)
        cond_fuerte = dsr_val > 0
        cond_debil = dsr_val > 0
        conditions["deflated_sharpe"] = {
            "fuerte": cond_fuerte, "debil": cond_debil,
            "dsr": dsr_val,
            "observed_sharpe": dsr.get("champion_avg_sharpe", 0),
            "details": dsr.get("dsr_results", {}),
        }
        print(f"  DSR(M=60) = {dsr_val:.4f} → "
              f"{'✓' if cond_fuerte else '~' if cond_debil else '✗'}")
    else:
        conditions["deflated_sharpe"] = {"fuerte": False, "debil": False, "dsr": -1}
        print("  NO DATA")

    # ── 4. PBO ──
    print("\n[4/8] Probability of Backtest Overfitting")
    pbo = load_json(PBO_FILE)
    if pbo and "pbo" in pbo:
        pbo_val = pbo["pbo"]
        cond_fuerte = pbo_val < 0.2
        cond_debil = pbo_val < 0.4
        hard_fail = pbo_val >= 0.5
        conditions["pbo"] = {
            "fuerte": cond_fuerte, "debil": cond_debil,
            "pbo": pbo_val,
            "hard_fail": hard_fail,
        }
        print(f"  PBO = {pbo_val:.3f} → "
              f"{'✓' if cond_fuerte else '~' if cond_debil else '✗'}"
              f"{' [HARD FAIL]' if hard_fail else ''}")
    else:
        conditions["pbo"] = {"fuerte": False, "debil": False, "pbo": 1.0, "hard_fail": True}
        print("  NO DATA")

    # ── 5. SPA ──
    print("\n[5/8] Superior Predictive Ability (Hansen SPA)")
    spa = load_json(SPA_FILE)
    if spa and "p_value" in spa:
        p_spa = spa["p_value"]
        cond_fuerte = p_spa < 0.05
        cond_debil = p_spa < 0.10
        soft_fail = p_spa >= 0.15
        conditions["spa_test"] = {
            "fuerte": cond_fuerte, "debil": cond_debil,
            "p_value": p_spa,
            "soft_fail": soft_fail,
        }
        print(f"  SPA p-value = {p_spa:.4f} → "
              f"{'✓' if cond_fuerte else '~' if cond_debil else '✗'}"
              f"{' [SOFT FAIL]' if soft_fail else ''}")
    else:
        conditions["spa_test"] = {"fuerte": False, "debil": False, "p_value": 1.0, "soft_fail": True}
        print("  NO DATA")

    # ── 6. Profit Factor (from CPCV) ──
    print("\n[6/8] Profit Factor (from CPCV / Purged CV)")
    pf_source = cpcv.get("distribution", {}).get("profit_factor", {}) if cpcv else {}
    pf_mean = pf_source.get("mean", 0) if pf_source else 0
    cond_fuerte = pf_mean > 1.5
    cond_debil = pf_mean > 1.3
    conditions["profit_factor"] = {
        "fuerte": cond_fuerte, "debil": cond_debil,
        "value": pf_mean,
    }
    print(f"  Mean PF = {pf_mean:.2f} → "
          f"{'✓' if cond_fuerte else '~' if cond_debil else '✗'}")

    # ── 7. Monte Carlo ruin (from FASE 6.5) ──
    print("\n[7/8] Monte Carlo ruin probability (FASE 6.5)")
    gate65 = load_json(GATE65)
    if gate65 and "monte_carlo" in gate65:
        mc = gate65["monte_carlo"]
        mc_details = mc.get("details", [])
        max_ruin = max(d.get("ruin_probability", 1.0) for d in mc_details) if mc_details else 1.0
        cond_fuerte = max_ruin < 0.01
        cond_debil = max_ruin < 0.05
        conditions["monte_carlo"] = {
            "fuerte": cond_fuerte, "debil": cond_debil,
            "max_ruin": max_ruin,
        }
        print(f"  Max ruin probability = {max_ruin:.4f} → "
              f"{'✓' if cond_fuerte else '~' if cond_debil else '✗'}")
    else:
        conditions["monte_carlo"] = {"fuerte": False, "debil": False, "max_ruin": 1.0}
        print("  NO DATA")

    # ── 8. Sharpe > 1 / Calmar > 1 (overall, from CPCV) ──
    print("\n[8/8] Overall Sharpe & Calmar (CPCV mean)")
    cpcv_dist = cpcv.get("distribution", {}) if cpcv else {}
    overall_sharpe = cpcv_dist.get("sharpe", {}).get("mean", 0) if cpcv_dist else 0
    overall_calmar = cpcv_dist.get("calmar", {}).get("mean", 0) if cpcv_dist else 0
    cond_s_fuerte = overall_sharpe > 1.5
    cond_s_debil = overall_sharpe > 1.0
    cond_c_fuerte = overall_calmar > 2.0
    cond_c_debil = overall_calmar > 1.0
    conditions["overall_sharpe"] = {
        "fuerte": cond_s_fuerte, "debil": cond_s_debil, "value": overall_sharpe,
    }
    conditions["overall_calmar"] = {
        "fuerte": cond_c_fuerte, "debil": cond_c_debil, "value": overall_calmar,
    }
    print(f"  Mean Sharpe = {overall_sharpe:.2f} (>{'1.5' if cond_s_fuerte else '1.0'}) → "
          f"{'✓' if cond_s_fuerte else '~' if cond_s_debil else '✗'}")
    print(f"  Mean Calmar = {overall_calmar:.2f} (>{'2.0' if cond_c_fuerte else '1.0'}) → "
          f"{'✓' if cond_c_fuerte else '~' if cond_c_debil else '✗'}")

    # ── HARD FAIL checks ──
    hard_fails = []
    if conditions.get("pbo", {}).get("hard_fail", False):
        hard_fails.append("PBO ≥ 0.5")
    dsr_primary = conditions.get("deflated_sharpe", {}).get("dsr", 1)
    if isinstance(dsr_primary, (int, float)) and dsr_primary <= 0:
        hard_fails.append("DSR ≤ 0")
    spa_p = conditions.get("spa_test", {}).get("p_value", 1)
    if isinstance(spa_p, (int, float)) and spa_p >= 0.15:
        hard_fails.append("SPA p-value ≥ 0.15")
    if overall_sharpe <= 1.0:
        hard_fails.append("Mean Sharpe < 1")
    pf = conditions.get("profit_factor", {}).get("value", 0)
    if pf <= 0:
        hard_fails.append("Expectancy ≤ 0")

    # ── Determine verdict ──
    cond_names = ["purged_cv_sharpe", "purged_cv_calmar", "cpcv_stability",
                  "deflated_sharpe", "pbo", "spa_test", "profit_factor",
                  "monte_carlo"]
    n_fuerte = sum(1 for c in cond_names if conditions.get(c, {}).get("fuerte", False))
    n_debil = sum(1 for c in cond_names if conditions.get(c, {}).get("debil", False))
    n_total = len(cond_names)

    print()
    print("=" * 60)
    print("GATE 6.75 — VERDICT")
    print("=" * 60)
    print()
    print(f"  Conditions (fuerte / débil):")
    for c in cond_names:
        cond = conditions.get(c, {})
        f = "✓" if cond.get("fuerte") else "~" if cond.get("debil") else "✗"
        d = "✓" if cond.get("debil") else "✗"
        label = c.replace("_", " ").title()
        print(f"    {label:25s}  fuerte={f}  débil={d}")

    print()
    print(f"  PASS fuerte count: {n_fuerte}/{n_total}")
    print(f"  PASS débil count:  {n_debil}/{n_total}")

    if hard_fails:
        print(f"  HARD FAILS: {', '.join(hard_fails)}")
        verdict = "HARD FAIL"
        verdict_color = "🔴"
    elif n_fuerte >= n_total - 1:
        verdict = "PASS FUERTE"
        verdict_color = "🟢"
    elif n_debil >= n_total - 1:
        verdict = "PASS DÉBIL"
        verdict_color = "🟡"
    else:
        verdict = "FAIL"
        verdict_color = "🔴"

    print()
    print(f"  {verdict_color} GATE 6.75: {verdict}")
    if verdict == "PASS FUERTE":
        print("  FASE 7 autorizada sin reservas.")
        print("  RandomForest sigue como champion; Complexity Premium aplica.")
    elif verdict == "PASS DÉBIL":
        print("  FASE 7 autorizada condicionalmente.")
        print("  RandomForest se mantiene como champion.")
        print("  Complexity Premium obligatorio: todo modelo avanzado debe")
        print("  superar al champion en Calmar, PF, DD, MC, Stress, etc.")
    elif verdict == "HARD FAIL":
        print("  FASE 7 CANCELADA. No avanzar a modelos profundos.")
        print("  Evidencia estadística insuficiente de alpha real.")
    else:
        print("  FASE 7 bloqueada. Revisar condiciones fallidas.")
        print("  Verificar si es necesario volver a FASE 5.5.")

    output = {
        "conditions": conditions,
        "n_fuerte": n_fuerte,
        "n_debil": n_debil,
        "n_total": n_total,
        "hard_fails": hard_fails,
        "verdict": verdict,
        "recommendation": "authorize_unrestricted" if verdict == "PASS FUERTE" else
                          "authorize_conditional" if verdict == "PASS DÉBIL" else
                          "blocked_hard_fail" if verdict == "HARD FAIL" else "blocked",
    }

    report_path = R675 / "gate_675.json"
    with open(report_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nReport: {report_path}")


if __name__ == "__main__":
    evaluate()
