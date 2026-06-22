"""Entry point: python -m scripts.forward_test [--symbol BTC] [--dry-run]"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from scripts.forward_test.config import (
    DEFAULT_INTERVAL,
    POLICY_DIR,
    THRESHOLDS,
    MAX_HOLD_BARS,
    LOOKAHEAD,
)
from scripts.forward_test.policy import PolicyV1
from scripts.forward_test.engine import ForwardTestEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

POLICY_FILENAME = "policy_v1.json"


def main():
    parser = argparse.ArgumentParser(description="Fase B — Forward Test")
    parser.add_argument("--symbol", default="BTC", choices=["BTC", "ETH", "SOL"],
                        help="Trading symbol (default: BTC)")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL,
                        help="Poll interval in seconds (default: 60)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run without placing real orders")
    parser.add_argument("--max-cycles", type=int, default=0,
                        help="Max poll cycles (0 = infinite)")
    parser.add_argument("--save-policy", action="store_true",
                        help="Write policy_v1.json and exit")
    parser.add_argument("--load-policy", type=str, default=None,
                        help="Path to policy_v1.json (default: policy/policy_v1.json)")
    args = parser.parse_args()

    # ── Policy ─────────────────────────────────────────────────────
    if args.save_policy:
        policy = PolicyV1.default(symbol=args.symbol)
        path = policy.save(POLICY_DIR)
        print(f"Policy saved to {path}")
        return

    load_path = args.load_policy or (POLICY_DIR / POLICY_FILENAME)
    if Path(load_path).exists():
        policy = PolicyV1.load(load_path)
        if policy.symbol != args.symbol:
            print(f"WARNING: policy symbol={policy.symbol} != --symbol={args.symbol}. Using --symbol.")
            policy = PolicyV1(
                symbol=args.symbol,
                entry_threshold=policy.entry_threshold,
                exit_threshold=policy.exit_threshold,
                max_hold_bars=policy.max_hold_bars,
            )
        print(f"Policy loaded: v{policy.version} ({policy.symbol})")
    else:
        policy = PolicyV1.default(symbol=args.symbol)
        print(f"Default policy: v{policy.version} ({policy.symbol})")

    print(f"  BUY  > {policy.entry_threshold}")
    print(f"  SELL < {policy.exit_threshold}")
    print(f"  HOLD max {policy.max_hold_bars} bars")

    # ── Engine ─────────────────────────────────────────────────────
    engine = ForwardTestEngine(
        symbol=args.symbol,
        policy=policy,
        dry_run=args.dry_run,
        poll_interval=args.interval,
    )
    engine.init()
    engine.run(max_cycles=args.max_cycles)

    # ── Final summary ─────────────────────────────────────────────
    s = engine.summary
    print(f"\n{'=' * 60}")
    print(f"Forward Test Complete — {s['symbol']}")
    print(f"  Cycles: {s['cycles']}")
    print(f"  Trades: {s['trades']}")
    print(f"  Frozen: {s['frozen']}  ({s['freeze_reason'] or 'N/A'})")
    if s['frozen']:
        sys.exit(1)


if __name__ == "__main__":
    main()
