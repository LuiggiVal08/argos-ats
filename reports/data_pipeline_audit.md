# Data Pipeline Audit
> Generated 2026-06-21 17:40:45 UTC

**Phase 1 Verdict**: BROKEN

CRITICAL=2, WARNING=4, OK=2

### All Findings

| Severity | Finding |
| --- | --- |
| CRITICAL | Redis stream has **mixed candle sources** — spot-like (tight range, ~400 BTC vol) vs tick-built (wide range, ~55K "BTC" vol). 164/200 recent candles are tick-built with inflated volume. |
| CRITICAL | Volume pipeline inconsistency: CandleBuilder accumulates **individual spot trade BTC quantities**. Training (CCXT `fetch_ohlcv` spot) returns 273 BTC/hr. Live tick-built volume averages **55,000/hr** — 200x higher. Possible causes: duplicate tick processing, double-counting, or CandleBuilder timing overlap. |
| WARNING | Volume differs 618.5% between CCXT and Redis — because most Redis candles are tick-built with inflated volume, not comparable to CCXT OHLCV |
| WARNING | Volume unit ambiguous: training median = 1,205 (likely BTC from CCXT spot). Tick-built live mean = 55,000. Even accounting for unit differences, the magnitude mismatch is 15-50x. |
| WARNING | Price differs 0.5151% between tick-built Redis candles and CCXT — tick-built candles have wider ranges (h-l spread up to 1.3%), suggesting they aggregate multiple data sources |
| WARNING | Pipeline may process duplicate ticks on WebSocket reconnect — no trade ID deduplication in `_tick_to_candle_loop` |
| OK | Spot-like candles (tight range, vol 50-1000) match CCXT spot within 0.05% price and 50% volume |
| OK | Candles use close_ts as timestamp — aligns with CandleBuffer.to_ohlcv_dicts() |
## 1.1 Volume: CCXT vs Redis
### Cross-validation: CCXT vs Redis

| Metric | Value |
| --- | --- |
| CCXT candles | 100 |
| Redis candles (deduped) | 99 |
| Common timestamps | 99 |
| Volume match (<1% diff) | 0/99 (0.0%) |
| Volume diff (mean ± std) | 618.45% ± 791.04% |
| Price diff (mean ± std) | 0.5151% ± 0.5260% |
| CCXT-only candles | 1 |
| Redis-only candles | 0 |
## 1.2 Duplicates & Gaps
### Redis Stream Integrity

| Metric | Value |
| --- | --- |
| Total entries (pre-dedup) | 1003 |
| Unique timestamps | 1003 |
| Duplicates removed | 0 |
| Duplicate rate | 0.0% |
Gaps (non-1h spacing): 0 occurrences
## 1.3 Volume Units
### Volume Unit Analysis

| Metric | Value |
| --- | --- |
| Avg volume (last 100) | 30381.69 BTC |
| Avg close (last 100) | $63799.06 |
| Implied notional | $1938323485.62 |
| Interpretation | Volume appears to be in BTC (contracts), not USDT |
### Volume Unit Verification

| Metric | Value |
| --- | --- |
| CCXT baseVolume (last) | 67434.84 BTC |
| CCXT quoteVolume (last) | $4326808220.72 |
| Redis candle avg volume | 30381.69 |
| Volume matches | BTC (baseVolume) |
### Volume Scale Verification

| Ratio | Value |
| --- | --- |
| Redis volume / CCXT baseVolume | 0.45x |
| Redis notional / CCXT quoteVolume | 0.45x |
## 1.4 Training vs Live: Volume Distribution
### Volume Drift

| Metric | Value |
| --- | --- |
| Training volume median (scaler) | 1205.30 |
| Training volume IQR (scaler) | 2330.17 |
| Live volume mean (last 1000) | 60588.29 |
| Live volume std (last 1000) | 30713.00 |
| Volume z-score | 25.48σ |
## 1.5 Funding Rate
### Live Funding Rate (CCXT)

| Metric | Value |
| --- | --- |
| Samples | 200 |
| Mean | 0.000009 |
| Std | 0.000047 |
| Min | -0.000123 |
| Max | 0.000100 |
| Median | 0.000013 |
| Last 8h entry (live) | NOT FOUND (likely pushed as stream, not key) |
## 1.6 Timestamp Field
### Candle Timestamp Verification

| Field | Raw | Interpretation |
| --- | --- | --- |
| open_ts | 1782057600000 | 2026-06-21 16:00:00 UTC |
| close_ts | 1782061200000 | 2026-06-21 17:00:00 UTC |
| is_complete | True |

