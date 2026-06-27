# CLASS ENCODING FORENSIC REPORT

**Date**: 2026-06-27
**Author**: AI forensic analysis
**Status**: **MISMATCH DETECTED — executed code ≠ stored source**

---

## 1. Source of Truth Chain

### 1.1 Training Encoding — `label_engine.py:29-31`

```
CLASS_SELL = 0   → class 0 → SELL
CLASS_HOLD = 1   → class 1 → HOLD
CLASS_BUY  = 2   → class 2 → BUY
```

### 1.2 Production Model — `models/production/btc/model.pkl`

```
model.classes_     = [0, 1, 2]        # sklearn sorted class labels
model.coef_.shape  = (3, 30)          # multinomial (3 classes, 30 features)
model.intercept_.shape = (3,)          # one intercept per class
model.solver       = lbfgs            # supports multinomial
model.C            = 10.0
model.n_features_in_ = 30
```

**sklearn contract**: `predict_proba` returns columns in `classes_` order:

```
probs[0] = P(class 0) = P(SELL)
probs[1] = P(class 1) = P(HOLD)
probs[2] = P(class 2) = P(BUY)
```

### 1.3 Live Inference Code — `streaming_inference.py:258-276`

```python
probs = self._lr_model.predict_proba(last_row)[0]          # ← returns [P(0), P(1), P(2)]

class_idx = int(np.argmax(probs))                           # step 3
prob_buy = float(probs[0]) if len(probs) > 0 else 0.0      # step 2 — MISNAMED
prob_sell = float(probs[1]) if len(probs) > 1 else 0.0     # step 2 — MISNAMED
prob_hold = float(probs[2]) if len(probs) > 2 else 0.0     # step 2 — MISNAMED

side = [SignalSide.BUY, SignalSide.SELL, SignalSide.HOLD][class_idx]  # step 4 — ROTATED
confidence = float(probs[class_idx])                        # step 5

if side == SignalSide.BUY and confidence < buy_threshold:
    side = SignalSide.HOLD                                   # step 6
if side == SignalSide.SELL and confidence < sell_threshold:
    side = SignalSide.HOLD                                   # step 6
```

### 1.4 Logged Data — `inference.log` + `inference_timeline.csv`

Both files show **correct** probabilities:

```
inference.log (17:00:04Z):
  prob_sell=0.051967  → matches P(SELL)  = probs[0]  ✓
  prob_hold=0.912675  → matches P(HOLD)  = probs[1]  ✓
  prob_buy=0.035358   → matches P(BUY)   = probs[2]  ✓
  predicted_class=1   → HOLD
  final_signal=HOLD

inference_timeline.csv (INF-000012):
  sell_probability=0.065633 → P(SELL) = probs[0]  ✓
  hold_probability=0.906306 → P(HOLD) = probs[1]  ✓
  buy_probability=0.028061  → P(BUY)  = probs[2]  ✓
  decision=HOLD
```

**Paradox**: The source code `prob_sell = probs[1]` would assign P(HOLD) to the
sell column. But the actual logged data has P(SELL) in the sell column.
The executed code from the old container (pre-17:35 restart) had the
**correct** mapping. The stored source on disk has the **rotated** mapping.

---

## 2. Transformation Trace

Run with `np.random.seed(1)`, `scaler.transform`, `model.predict_proba`:

```
model.classes_      = [0, 1, 2]
predict_proba(X)    = [0.072637, 0.743441, 0.183921]
  probs[0] = P(0)   = P(SELL) = 0.072637
  probs[1] = P(1)   = P(HOLD) = 0.743441
  probs[2] = P(2)   = P(BUY)  = 0.183921
```

### Transformation Table

| Stage | Code Ref | Value | Correct? |
|-------|----------|-------|----------|
| `model.classes_` | sklearn attr | `[0, 1, 2]` | ✅ |
| `probs[0]` | skl `predict_proba` | P(0)=P(SELL)=0.072637 | ✅ |
| `probs[1]` | skl `predict_proba` | P(1)=P(HOLD)=0.743441 | ✅ |
| `probs[2]` | skl `predict_proba` | P(2)=P(BUY)=0.183921 | ✅ |
| `prob_buy` | line 259: `= probs[0]` | 0.072637 (actually P(SELL)) | **❌ named "buy", value is SELL prob** |
| `prob_sell` | line 260: `= probs[1]` | 0.743441 (actually P(HOLD)) | **❌ named "sell", value is HOLD prob** |
| `prob_hold` | line 261: `= probs[2]` | 0.183921 (actually P(BUY)) | **❌ named "hold", value is BUY prob** |
| `class_idx` | line 258: `argmax(probs)` | 1 | ✅ (model predicts class 1 = HOLD) |
| selected class | `classes_[class_idx]` | class 1 = HOLD | ✅ |
| `side` | line 263: `list[class_idx]` | `SignalSide.SELL` | **❌ model says HOLD, code emits SELL** |
| `confidence` | line 264: `probs[class_idx]` | 0.743441 (P(HOLD)) | ✅ value, but used for wrong side |
| threshold check | line 274: `SELL AND 0.74 < 0.5` | FALSE | ❌ SELL not demoted |
| **final signal** | line 275-276 | **SELL** | **❌ should be HOLD** |

### What the model actually said vs what the code emitted

| Scenario | Model says | Code emits | Correct? |
|----------|-----------|------------|----------|
| argmax=0 (SELL) | `SELL` | `BUY` | ❌ |
| argmax=1 (HOLD) | `HOLD` | `SELL` | ❌ |
| argmax=2 (BUY) | `BUY` | `HOLD` | ❌ |

**Root cause**: Cyclic rotation by −1 index. `list[class_idx]` should be
`list[(class_idx + 1) % 3]` OR the list should be `[SELL, HOLD, BUY]`.

---

## 3. 17:35 SELL Trade Reconstruction

**Pre-restart** (17:00:04Z — old process):
- Raw probs: `[P(SELL)=0.052, P(HOLD)=0.913, P(BUY)=0.035]`
- Running code had **correct** mapping → `side = HOLD` → no signal ✓

**Post-restart** (17:35:32Z — new process):
- Same candle, same raw probs: `[P(SELL)=0.052, P(HOLD)=0.913, P(BUY)=0.035]`
- Code from disk has **rotated** mapping:
  - `class_idx = argmax = 1` (class 1 = HOLD)
  - `side = [BUY, SELL, HOLD][1] = SELL`
  - `confidence = 0.913` (= P(HOLD), used as "SELL confidence")
  - Threshold: `0.913 < 0.5` → FALSE → SELL kept
- **Trade executed: SELL BTC/USDT** ❌

The 0.913 confidence logged as "SELL" is numerically identical to P(HOLD)
from the pre-restart inference — confirming the rotation.

---

## 4. Git Commit Verification

| Location | Commit | Branch |
|----------|--------|--------|
| Local source tree | `d9a1af0` | `feature/h7-distribution-shift-doc-and-tracking` |
| Container `/app/app/` | mounted bind volume → same as local | same |
| Container `.git` | not present (production container) | N/A |

**Verdict**: Container runs the same code as local. The rotated mapping at
`streaming_inference.py:259-263` IS what the container executes
post-restart.

---

## 5. Affected Inference IDs

| Seq ID | Timestamp (UTC) | Signal (old code) | Signal (current code) | Affected? |
|--------|-----------------|-------------------|----------------------|-----------|
| INF-000001 | 03:00:01 | HOLD | SELL | ❌ would be wrong |
| INF-000002 | 06:00:02 | HOLD | SELL | ❌ would be wrong |
| INF-000003 | 07:00:02 | HOLD | SELL | ❌ would be wrong |
| INF-000004 | 10:00:02 | HOLD | SELL | ❌ would be wrong |
| INF-000005 | 10:17:39 | HOLD | SELL | ❌ would be wrong |
| INF-000006 | 10:30:57 | HOLD | SELL | ❌ would be wrong |
| INF-000007 | 11:00:03 | HOLD | SELL | ❌ would be wrong |
| INF-000008 | 12:00:03 | HOLD | SELL | ❌ would be wrong |
| INF-000009 | 13:00:03 | HOLD | SELL | ❌ would be wrong |
| INF-000010 | 14:00:03 | HOLD | SELL | ❌ would be wrong |
| INF-000011 | 15:00:05 | HOLD | SELL | ❌ would be wrong |
| INF-000012 | 16:00:05 | HOLD | SELL | ❌ would be wrong |
| INF-000013 | 17:00:04 | HOLD | SELL | ❌ would be wrong |
| **INF-000014** | **17:35:32** | **— (new process)** | **SELL** | **❌ ACTUAL TRADE EXECUTED** |

**Historical impact**: All 13 pre-restart inferences emitted correct HOLD
signals because the running binary used correct mappings (despite source
code showing wrong ones). Only post-restart inferences are affected.

---

## 6. Runtime Forensic Capture (2026-06-27T18:29:06Z)

After adding a forensic `logger.error` and restarting the container,
the executing pipeline produced:

```
model_classes=[0, 1, 2]
probs=[0.0359, 0.9101, 0.0541]
  probs[0]=0.0359 = P(class_0=SELL)
  probs[1]=0.9101 = P(class_1=HOLD)
  probs[2]=0.0541 = P(class_2=BUY)
class_idx=1
selected_class=1 (HOLD)
side=SignalSide.SELL
prob_sell=0.9101   ← probs[1] = P(HOLD), not P(SELL)
prob_hold=0.0541   ← probs[2] = P(BUY), not P(HOLD)
prob_buy=0.0359    ← probs[0] = P(SELL), not P(BUY)
```

**Bug confirmed CASE A**: `selected_class=1 (HOLD)` but `side=SELL`.
The rotation is proven at runtime inside the production container.

---

## 7. Summary

| Check | Status |
|-------|--------|
| `model.classes_` matches training encoding | ✅ `[0,1,2]` = `[SELL,HOLD,BUY]` |
| `predict_proba` returns `[P(SELL), P(HOLD), P(BUY)]` | ✅ |
| Variable assignment matches column order | **❌ rotated** (`probs[0]`→`buy`, `probs[1]`→`sell`, `probs[2]`→`hold`) |
| `class_idx` → `SignalSide` mapping matches encoding | **❌ rotated** (`0`→`BUY`, `1`→`SELL`, `2`→`HOLD`) |
| Pre-restart logged data correct | ✅ (binary used different code) |
| Post-restart code on disk matches injected binary | ✅ (bind mount, `d9a1af0`) |
| **Runtime forensic capture** | **✅ BUG CONFIRMED** (class=1=HOLD → side=SELL) |

**Root cause**: `streaming_inference.py` lines 259-261 name variables
indices 0→BUY, 1→SELL, 2→HOLD when sklearn returns indices
0→SELL, 1→HOLD, 2→BUY. Line 263 has the same rotation:
`[BUY, SELL, HOLD]` should be `[SELL, HOLD, BUY]`.

---

## 8. Remediation (recommendation, not applied)

The following changes to `streaming_inference.py` would fix the encoding:

```
- prob_buy  = float(probs[0])
- prob_sell = float(probs[1])
- prob_hold = float(probs[2])
+ prob_sell = float(probs[0])   # probs[0] = P(SELL)
+ prob_hold = float(probs[1])   # probs[1] = P(HOLD)
+ prob_buy  = float(probs[2])   # probs[2] = P(BUY)

- side = [SignalSide.BUY, SignalSide.SELL, SignalSide.HOLD][class_idx]
+ side = [SignalSide.SELL, SignalSide.HOLD, SignalSide.BUY][class_idx]
```
