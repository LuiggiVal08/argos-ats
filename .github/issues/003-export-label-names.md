## Summary

`export_training_data.py` prints label statistics with incorrect column-to-label mapping.

## Current Code

```python
# export_training_data.py:181-183
print(f"  -> {len(features_raw)} samples, ... "
      f"BUY={float((targets[:,0].sum()/len(targets))*100):.1f}% "
      f"SELL={float((targets[:,1].sum()/len(targets))*100):.1f}% "
      f"HOLD={float((targets[:,2].sum()/len(targets))*100):.1f}%")
```

## Problem

Column 0 = SELL, Column 1 = HOLD, Column 2 = BUY (per global standard).

But the print labels them as BUY, SELL, HOLD respectively.

## Impact

Cosmetic only — affects printed output, not exported data. However, it can mislead anyone reading the export logs.

## Acceptance Criteria

- [ ] Fix print labels to match global standard: column 0=SELL, 1=HOLD, 2=BUY
