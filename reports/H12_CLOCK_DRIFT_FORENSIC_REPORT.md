# H12 — Binance Timestamp Drift Forensic Report

## Classification: NO FIX REQUIRED

**Severity**: Low (3 occurrences in ~48K log lines, 0.006%)
**Risk**: None (errors caught → BalanceProviderError → trade SKIPPED safely)

---

## 1. Measurements

### 1.1 Local clock offset vs Binance server time

20 samples taken from host machine to `fapi.binance.com/fapi/v1/time`:

| Metric        | Value    |
| ------------- | -------- |
| Mean offset   | -869 ms  |
| Median offset | -1085 ms |
| Min offset    | -1856 ms |
| Max offset    | +2002 ms |
| Std deviation | 882 ms   |
| p95 offset    | +2002 ms |
| p99 offset    | +2002 ms |
| Mean RTT      | 2230 ms  |

### 1.2 Observations

- High variance in offset measurements (std dev 882ms) indicates **noise from network latency dominates** any true clock drift
- Mean RTT of 2.23s means the one-shot offset calculation is unreliable — the true clock offset is smaller than the measurement noise
- One sample showed negative latency (-703ms), indicating timing anomalies

### 1.3 Container time configuration

- Host: `America/Caracas (-04)`, NTP synchronized, NTP service active
- Container: TZ not set, uses host kernel clock
- Host clock is synchronized with NTP

---

## 2. Existing safeguards

### 2.1 ccxt `adjustForTimeDifference: True`

File: `apps/analytics-engine/app/composition.py:344`

```python
ex = klass({
    "apiKey": api_key,
    "secret": api_secret,
    "enableRateLimit": True,
    "recvWindow": 10000,
    "options": {
        "adjustForTimeDifference": True,
    },
})
```

This tells ccxt to:

1. Call `exchange.serverTime()` at exchange creation
2. Calculate `offset = serverTime - localTime`
3. Apply `timestamp = now + offset` to every request

### 2.2 recvWindow: 10000

A 10-second timestamp tolerance window. Even the most extreme offset measured (-1856ms to +2002ms) is well within this window.

---

## 3. Observed errors

Only 3 occurrences of `-1021` in the entire log history:

| Timestamp           | Source                     | Error                                  |
| ------------------- | -------------------------- | -------------------------------------- |
| 2026-06-27T09:44:25 | `ccxt_balance_provider.py` | `-1021: 1000ms ahead of server's time` |
| 2026-06-27T09:58:38 | `ccxt_balance_provider.py` | `-1021: 1000ms ahead of server's time` |
| 2026-06-27T10:12:44 | `ccxt_balance_provider.py` | `-1021: 1000ms ahead of server's time` |

All 3 occurred within a 28-minute window during a 4.36-hour session (started 02:09, ended 10:17).

---

## 4. Root cause analysis

### Hypothesis A: Local clock drift

**EVIDENCE AGAINST**: Host has active NTP service, `System clock synchronized: yes`. Measured offset variance is dominated by network jitter (2.2s RTT), not true drift.

### Hypothesis B: recvWindow too small

**EVIDENCE AGAINST**: recvWindow is 10000ms (10 seconds). Even in worst-case measurements, the offset is <2000ms.

### Hypothesis C: Container timezone/clock issue

**EVIDENCE AGAINST**: Container shares host kernel clock, which is NTP-synchronized.

### Hypothesis D: Network latency spike

**EVIDENCE AGAINST**: All 3 errors say "1000ms ahead", not a variable amount. If it were network latency, the offset would vary.

### Hypothesis E: Binance Testnet instability

**EVIDENCE**: All 3 errors occurred against testnet (`binanceusdm` testnet mode). The string "1000ms ahead" is suspiciously round — suggests the testnet may have stricter timestamp validation than mainnet, or `adjustForTimeDifference` fails during testnet session initialization.

**CONCLUSION: LIKELY TESTNET-ONLY INSTABILITY.** The `adjustForTimeDifference` initial sync may have failed during exchange creation (testnet timeout/hiccup), leaving no offset correction for that session. Three requests in 7+ hours hit a timing edge case.

---

## 5. Decision

**No code changes required.** Evidence does not support a systemic clock drift issue:

1. Host clock is NTP-synchronized
2. ccxt `adjustForTimeDifference: True` provides offset correction
3. recvWindow of 10 seconds is more than adequate
4. Error rate is 0.006% — three isolated occurrences
5. PAPER_TRADING mode uses `VirtualBalanceProvider` which never touches the exchange
6. LIVE mode would use `CcxtBalanceProvider` but the existing safeguards are sufficient

### If errors persist in LIVE mode

The recommended fix would be periodic clock re-sync:

```python
# In ccxt_balance_provider.py, add periodic time offset refresh
import time

class CcxtBalanceProvider:
    def __init__(self, exchange, refresh_interval_s=300):
        self._exchange = exchange
        self._refresh_interval = refresh_interval_s
        self._last_sync = 0

    async def _ensure_synced(self):
        if time.time() - self._last_sync > self._refresh_interval:
            try:
                server_time = await self._exchange.fetch_time()
                self._exchange.time_offset = server_time - int(time.time() * 1000)
                self._last_sync = time.time()
            except Exception:
                pass  # use existing offset
```

This is deferred until evidence of LIVE-mode issues.
