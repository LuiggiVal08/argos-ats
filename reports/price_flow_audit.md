# P0 — Price Flow Audit Report

## Hypothesis
`ExecutionSignal.price` llega como `None` y `ExecuteSignalUseCase` falla con "signal has no price — price provider required for live execution".

## Verification

### Full Trace

```
StreamingInferencePipeline.predict()
  → StreamingInferenceResult.signal: TradingSignal
  → TradingSignal has NO price field (trading_signal.py:34-38)
    fields: side, confidence, timestamp, model_version, metadata

StreamingSignalProcessor.process(signal: TradingSignal)
  → ExecutionSignal created at line 81-93
  → hardcoded: price=None  (streaming_signal_processor.py:87)
  → TradingSignal has no price to pass through

ExecutionGuard.execute(signal: ExecutionSignal)
  → passes signal directly to ExecuteSignalUseCase.execute()

ExecuteSignalUseCase.execute(signal: ExecutionSignal)
  → line 142: entry_price = signal.price → None
  → line 143-146: raises ExecuteSignalError("signal has no price")
```

### Root Cause
`StreamingSignalProcessor.process()` line 87: `price=None` is hardcoded. The upstream `TradingSignal` (from the model) has no concept of price — it only carries `side`, `confidence`, `timestamp`, `model_version`, `metadata`.

### Downstream Impact
- `ExecuteSignalUseCase` raises `ExecuteSignalError` before reaching any exchange
- `ExecutionGuard` catches this as `consecutive_failures++`
- After 3 failures → 30s soft pause → infinite failure loop
- **No orders ever execute in streaming mode**

### Available Price Data
`StreamingInferenceResult.candle_close` (streaming_inference.py:39) contains the close price of the last candle. This is available in `_decision_loop` as `result.candle_close` (float).

### Fix
1. Add `price: Decimal | None = None` parameter to `StreamingSignalProcessor.process()`
2. Pass `Decimal(str(result.candle_close))` from `_decision_loop` in main.py

## Verdict
**BUG CONFIRMED** — `ExecutionSignal.price` is always `None` in the streaming pipeline.
