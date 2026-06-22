# QV2 Phase 3.75 — Cross-Market Validation

## Goal

Determine whether the signal discovered in BTC/USDT (53 features, stride=5, embargo=1)
is structural across crypto markets or specific to BTC.

## Markets

| Symbol | Status |
|--------|--------|
| BTC/USDT | Control — must reproduce Phase 3.5 |
| ETH/USDT | Primary target |
| SOL/USDT | Secondary target |
| NASDAQ futures | PENDING — no data source configured |

## Protocol

Identical to Phase 3.5:
- lookahead=5, stride=5, embargo=1
- 53 features (20 TA + 30 MTF + 3 funding)
- Same models, baselines, shuffle tests, metrics
- No tuning per symbol

## Execution order

1. `python3 -m experiments.quant_validation_v2_phase375.fetch_data`
2. `python3 -m experiments.quant_validation_v2_phase375.run`
3. `python3 -m experiments.quant_validation_v2_phase375.summary`

## Veredicts

| Condition | Verdict |
|-----------|---------|
| ETH + SOL pass gates (F1>0.60, AUC>0.70, R²>0.05, shuffle>0) | STRUCTURAL ALPHA |
| BTC passes, mix of ETH/SOL | PARTIAL GENERALIZATION |
| Only BTC passes | BTC-SPECIFIC ALPHA |
| BTC fails or all collapse | DATASET ARTIFACT |
