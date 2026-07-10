# INCIDENT-001 — Class Encoding Rotation

**Date**: 2026-06-27
**Status**: Resolved (position closed, fix deployed)

---

## Summary

| Field | Value |
|---|---|
| ID | INCIDENT-001 |
| Type | Software Bug — Signal Encoding |
| Severity | HIGH (invalid trade executed) |
| Root cause | `streaming_inference.py:259-263` |
| Fix | Corrected `prob_buy`/`prob_sell`/`prob_hold` index mapping + `[SELL,HOLD,BUY]` list order |

---

## Timeline

| Time (UTC) | Event |
|---|---|
| 2026-06-27 17:35:32 | Post-restart container loads code with rotated mapping |
| 2026-06-27 17:35:32 | INF-000014: model predicts HOLD (P=0.913), code emits SELL |
| 2026-06-27 ~17:35:33 | SELL market order executed on Binance Futures Demo — 0.1644 BTC at ~60,495 USDT |
| 2026-06-27 18:29:06 | Forensic capture confirms bug at runtime |
| 2026-06-27 ~18:40 | `git checkout --force dev` removes symlink, replaces model with binary |
| 2026-06-27 ~19:05 | Guard rail rejects binary model at restart (classes=[0,1], not [0,1,2]) |
| 2026-06-27 19:20-20:20 | Hotfix deployed: contract assertions, production model path, encoding fix |
| 2026-06-27 20:22:58 | Erroneous position closed (BUY market, reduceOnly) |
| 2026-06-27 20:22:58 | Fix validated: container healthy, model loads correctly, encoding correct |

---

## Trade Detail

| Field | Value |
|---|---|
| Symbol | BTC/USDT |
| Direction | SHORT (SELL) |
| Entry price | 60,495 USDT |
| Size | 0.1644 BTC |
| Notional | ~9,945 USDT |
| Exit price | ~60,246.6 USDT (market) |
| Gross P&L | +40.84 USDT |
| Est. fees (2× taker 0.04%) | ~7.94 USDT |
| **Net P&L** | **+32.90 USDT** |

**Note**: The invalid trade happened to be profitable because price moved
against the short entry. This is random — the bug could equally have
caused a loss.

**Operational Cost**: $0 (testnet). In production, this would have been
a real loss/gain from an invalid signal.

---

## Affected Inferences

| INF ID | Timestamp | Model Prediction | Code Output | Actual Trade? |
|--------|-----------|-----------------|-------------|---------------|
| INF-000001–INF-000013 | 03:00–17:00 | HOLD (old binary, correct map) | HOLD | No |
| INF-000014 | 17:35:32 | HOLD (new binary, rotated map) | **SELL** | **Yes** |

Historical: 13 pre-restart inferences used the old binary with correct
mapping. Only INF-000014 (post-restart) was affected.

---

## P&L Detail

### Trade Execution

| Leg | Time (UTC) | Side | Price | Size | Notional |
|-----|-----------|------|-------|------|----------|
| Entry | 2026-06-27 17:35:32 | SELL | 60,495.00 | 0.1644 BTC | 9,945 USDT |
| Exit | 2026-06-27 20:22:58 | BUY | 60,246.60 | 0.1644 BTC | 9,905 USDT |

### P&L Breakdown

| Concept | Calculation | Amount (USDT) |
|---------|-------------|---------------|
| Gross P&L | 0.1644 × (60,495 − 60,246.60) | +40.84 |
| Entry fee (0.04% taker) | 9,945 × 0.0004 | −3.98 |
| Exit fee (0.04% taker) | 9,905 × 0.0004 | −3.96 |
| **Net P&L** | | **+32.90** |
| Initial balance | pre-bug snapshot | 5,000.00 |
| Balance after close | | 5,032.90 |

### Operational P&L (for risk tracking)

| Metric | Value |
|--------|-------|
| Operational P&L (INCIDENT-001) | +32.90 USDT |
| Return on incident capital | +0.66% |
| Duration (open → close) | 2h 47min |
| Was trade valid? | **No** — signal was HOLD, code emitted SELL (class encoding rotation) |
| P&L classification | **Technology Risk P&L** — not a trading signal outcome |

**Note**: The positive P&L is purely coincidental (price moved against the short after entry). The bug could equally have produced a loss. This $32.90 should be recorded in forward-testing metrics as **operational/technology risk cost**, not attributed to strategy performance. Per your methodology:
- Pre-bug: 5,000 USDT (capital)
- During bug: 5,032.90 USDT (contaminated by invalid trade)
- Next validation epoch: **reset to 5,000 USDT** before starting Epoch 2 metrics

---

## Root Cause

**File**: `apps/analytics-engine/app/infrastructure/trading/streaming_inference.py`
**Lines**: 259-263

```python
# WRONG (pre-fix):
prob_buy  = float(probs[0])   # probs[0] = P(SELL), not P(BUY)
prob_sell = float(probs[1])   # probs[1] = P(HOLD), not P(SELL)
prob_hold = float(probs[2])   # probs[2] = P(BUY), not P(HOLD)
side = [SignalSide.BUY, SignalSide.SELL, SignalSide.HOLD][class_idx]
```

sklearn `predict_proba` returns columns in `model.classes_` order `[0,1,2]`
where class 0 = SELL, class 1 = HOLD, class 2 = BUY (per training encoding).

The code indexed them as 0→BUY, 1→SELL, 2→HOLD, creating a cyclic -1 rotation.

**Fix** (deployed 2026-06-27 ~19:20):
```python
# CORRECT (post-fix):
prob_sell = float(probs[0])   # probs[0] = P(SELL)
prob_hold = float(probs[1])   # probs[1] = P(HOLD)
prob_buy  = float(probs[2])   # probs[2] = P(BUY)
side = [SignalSide.SELL, SignalSide.HOLD, SignalSide.BUY][class_idx]
```

---

## Fixes Applied

| Fix | File | Status |
|---|---|---|
| Encoding mapping corrected | `streaming_inference.py:259-263` | ✅ |
| Model loader → `/production/{symbol}/` | `streaming_inference.py` | ✅ |
| Contract assertions (classes, features, version) | `streaming_inference.py:load_checkpoint` | ✅ |
| ETH/SOL disabled (incompatible models) | `composer_activation.py:_KNOWN_SYMBOLS` | ✅ |
| Wiring fix: `verify_sl_after_placement` param | `execute_signal.py:__init__` | ✅ |

---

## Lessons

1. **Class encoding must be a single source of truth**: Training (`label_engine.py`)
   and inference (`streaming_inference.py`) shared the encoding implicitly.
   No test verified they matched.

2. **Guard rails prevented worse damage**: The model contract assertions
   (classes=[0,1,2], features=30, version prefix) blocked the binary model
   from loading, preventing silent deployment with wrong model.

3. **Symlinks hide divergence**: `models/btc -> production/btc` was not
   versioned. `git checkout` silently replaced it with the tracked binary
   directory. Source of truth must be in git.

4. **`test_label_encoding_contract` was correct**: The failing test was
   detecting exactly this bug. Should be a P0 deployment gate.
