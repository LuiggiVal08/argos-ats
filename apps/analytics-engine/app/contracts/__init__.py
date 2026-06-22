"""Contract validators — single source of truth is contracts/*.json at project root.

Usage:
    from app.contracts import validate_candle, Candle

    payload = {"symbol": "BTC/USDT", ...}
    errors = validate_candle(payload)
    if errors:
        raise ValueError(f"contract violation: {errors}")

    c = Candle(**payload)  # typed dataclass
"""
