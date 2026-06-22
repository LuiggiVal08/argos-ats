---
description: Audita la lógica de riesgo y el Circuit Breaker contra el spec
agent: plan
---

You are performing a read-only audit of the risk management subsystem in argos-ats.

Re-read @spec.md section 3 (decision logic) and the user stories in section 5: Historia 2 (position sizing) and Historia 3 (circuit breaker).

Check the code for:
1. **Position sizing**: Loss per trade must never exceed 1% of free balance. The stop-loss distance must be derived from ATR, not a fixed percentage.
2. **Circuit breaker**: When daily drawdown >= 5%, the system must (a) cancel all open orders, (b) close all open positions at market, (c) flip `ENVIRONMENT_MODE` to `PASIVO`, (d) log the lockout, (e) halt the trading loop.
3. **Sad paths**: Network timeouts from CCXT, invalid balance of 0, sub-minimum lot size — all must abort cleanly before touching the exchange.
4. **Secret hygiene**: No API keys, secrets, or signatures hardcoded. They must come from env vars injected at runtime (spec section 4).

Output a structured report:
- Verdict: SAFE / NEEDS FIXES / CRITICAL
- Findings: file:line + risk + remediation
Do not edit any files.
