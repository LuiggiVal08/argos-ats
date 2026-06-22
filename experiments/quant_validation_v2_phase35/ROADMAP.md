# QV2 Phase 3.5 — Non-overlapping Labels

## Goal

Falsify whether the signal observed in QV2 (53 features = TA + MTF 4h/1d + funding)
is real or an artifact of overlapping labels (lookahead=5, stride=1 → ~80% autocorrelation).

## Design

Same feature space, models, metrics as QV2. Only change: label construction.

### PRIMARY (decides verdict)
- lookahead=5, stride=5
- No overlapping future between consecutive samples
- Embargo: `ceil(lookahead/stride)` = 1 sample removed from each fold's train set

### Sanity checks (diagnostic only, NOT for decision)
- lookahead=1, stride=1 (≈ QV2 replication)
- lookahead=3, stride=3 (intermediate)

## Expected outcomes

**H1 SURVIVES** if:
- Binary F1 > 0.60
- AUC > 0.70
- Regression R² > 0.05
- Shuffle delta positive
- At least 2/3 framings maintain advantage vs noise

**H0 EXTENDED** if:
- F1 ≈ 0.5, AUC ≈ 0.5, R² ≈ 0, shuffle delta ≈ 0

## Next step after

If H1 SURVIVES → cross-market validation (ETH, SOL, NASDAQ futures).
If H0 EXTENDED → close this line of investigation.
