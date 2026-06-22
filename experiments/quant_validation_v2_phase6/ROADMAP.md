# Phase 6 — Probability Calibration

## Goal
Assess how well the model's probability estimates reflect true frequencies. Compute Brier score, ECE, reliability curves, and apply Platt scaling if miscalibrated.

## Input
- Phase 5 predictions parquet: `btc_predictions.parquet`, `eth_predictions.parquet`, `sol_predictions.parquet`
- Columns: `y_true` (0/1), `y_proba` (probability of BUY class)

## Output
- `calibration.json` — per-symbol + pooled metrics

## Verdicts
- CALIBRATED: ECE < 0.05
- OVERCONFIDENT: mean_conf > mean_acc + 0.05
- UNDERCONFIDENT: mean_acc > mean_conf + 0.05
- FIXABLE: Platt scaling improves ECE by >20%
