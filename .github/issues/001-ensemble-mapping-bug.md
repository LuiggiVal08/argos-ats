## Summary

`_ensemble_decision_raw()` in `predict_ensemble.py` interprets the MetaModel output with an incorrect index mapping.

## Current Behavior

```python
# predict_ensemble.py:374-376
buy_p  = float(probs[0])  # This is P(SELL), not P(BUY)
sell_p = float(probs[1])  # This is P(HOLD), not P(SELL)
hold_p = float(probs[2])  # This is P(BUY),  not P(HOLD)
```

The MetaModel (XGBoost) was trained with labels from `create_targets()`:
- 0 = SELL [1,0,0]
- 1 = HOLD [0,1,0]
- 2 = BUY  [0,0,1]

So `probs[0]` = P(SELL), `probs[1]` = P(HOLD), `probs[2]` = P(BUY).

But `_ensemble_decision_raw` reads them as 0=BUY, 1=SELL, 2=HOLD — inverted.

## Impact

The `POST /model/predict` endpoint returns incorrect signals when the ensemble path is used. This does NOT affect the production streaming loop (`StreamingInferencePipeline` uses LogisticRegression with correct mapping).

## Acceptance Criteria

- [ ] Use the same mapping as `StreamingInferencePipeline`: 0=SELL, 1=HOLD, 2=BUY
- [ ] Add test that validates `predict_proba()` → `SignalSide` correspondence
- [ ] Remove magic index numbers — use explicit mapping
- [ ] Verify with `model.classes_` check like the safety check in `streaming_inference.py:298-305`

## Related

- `streaming_inference.py:295` — correct reference implementation
- `streaming_inference.py:298-305` — safety check pattern to follow
