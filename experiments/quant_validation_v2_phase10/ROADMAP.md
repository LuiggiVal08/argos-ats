# Phase 10 — Anti-Leakage Audit

## Goal
Exhaustively audit the QV2 pipeline for any form of lookahead bias or data leakage.

## Scope
10.1 Feature audit — 53 features, source, window, first valid row
10.2 MTF alignment — 4h and 1d feature timestamps vs bar timestamps
10.3 Funding alignment — funding_rate timestamps vs bar timestamps
10.4 Target audit — forward_return uses only close[t+5]
10.5 Walk-forward audit — no overlap, embargo respected

## Verdict
Each check reports PASS / WARNING / HARD_FAIL with concrete explanation.
