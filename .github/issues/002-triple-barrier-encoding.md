## Summary

`create_targets_triple_barrier()` uses a different encoding than the global standard defined by `create_targets()`.

## Global Standard (from `create_targets()`)

- 0 = SELL [1,0,0]
- 1 = HOLD [0,1,0]
- 2 = BUY  [0,0,1]

## Current Triple Barrier Encoding

```python
# data_preprocessor.py:356 — TP hit first (BUY)
targets[i] = [1.0, 0.0, 0.0]  # Index 0 = SELL per standard

# data_preprocessor.py:360 — SL hit first (SELL)
targets[i] = [0.0, 1.0, 0.0]  # Index 1 = HOLD per standard

# data_preprocessor.py:365 — Neither hit (HOLD)
targets[i] = [0.0, 0.0, 1.0]  # Index 2 = BUY per standard
```

The triple barrier function has BUY and SELL swapped compared to the global standard.

## Impact

Currently low — `create_targets_triple_barrier()` is only called in `scripts/export_training_data.py`, not in the production training pipeline. However, the inconsistent encoding is a risk if this function is used in future training.

## Acceptance Criteria

- [ ] Align `create_targets_triple_barrier()` encoding with `create_targets()`
- [ ] Update docstring to match global standard
- [ ] Verify `scripts/export_training_data.py` labels are consistent

## Note

Commit `f875c6d` fixed `create_targets()` but did not touch `create_targets_triple_barrier()`.
