# QV2 Phase 3.8 — Cross-Exchange Validation

## Goal

Determine whether the structural alpha discovered in QV2 Phase 3.5 / Phase 3.75
depends on Binance's microstructure or survives exchange-level changes.

**Question**: Does the same 53-feature MTF+Funding signal survive Bybit and OKX
without any modification to the protocol?

## Markets

| Asset | Binance | Bybit | OKX |
|-------|---------|-------|-----|
| BTC/USDT | Control | ✓ | ✓ |
| ETH/USDT | ✓ | ✓ | ✓ |
| SOL/USDT | ✓ | ✓ | ✓ |

## Protocol

Identical to Phase 3.5 (frozen):
- 53 features (20 TA + 30 MTF + 3 funding)
- lookahead=5, stride=5, embargo=1
- Same models, baselines, shuffle tests, walk-forward
- No tuning, no threshold changes, no feature selection per exchange

## Execution order

1. `python3 -m experiments.quant_validation_v2_phase38.fetch_data`
2. `python3 -m experiments.quant_validation_v2_phase38.run`
3. `python3 -m experiments.quant_validation_v2_phase38.summary`

BTC+Binance control must pass before any other combination runs.

## Verdicts

| Pattern | Verdict |
|---------|---------|
| ≥6/9 pass with balanced degradation | EXCHANGE-INVARIANT ALPHA |
| Most pass but systematic degradation on one exchange | PARTIAL INVARIANCE |
| Only Binance passes or signal concentrated in 1 exchange | EXCHANGE-SPECIFIC ALPHA |
| Control fails or 0-1 combos pass | MICROSTRUCTURE ARTIFACT |
