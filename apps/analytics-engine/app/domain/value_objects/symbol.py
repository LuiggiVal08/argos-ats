"""Symbol — value object for a trading pair.

Encapsulates symbol validation and parsing so that ``Symbol("BTC/USDT").quote_currency``
returns ``"USDT"`` (replacing ad-hoc ``split("/")`` calls across the codebase).
"""
from __future__ import annotations

from typing import ClassVar, Final

_INVALID_SYMBOL_MSG: Final = (
    "Invalid symbol format: {symbol!r}. Expected format: BASE/QUOTE"
)


class InvalidSymbolError(ValueError):
    """Raised when a Symbol string is malformed."""


class Symbol:
    """Trading pair value object.

    Usage::

        s = Symbol("BTC/USDT")
        s.base        # "BTC"
        s.quote       # "USDT"
        s.quote_currency  # "USDT" (alias for clarity in balance context)
        str(s)        # "BTC/USDT"
    """

    __slots__ = ("_raw", "_base", "_quote")

    SEPARATOR: ClassVar[str] = "/"

    def __init__(self, raw: str) -> None:
        if not isinstance(raw, str) or not raw:
            raise InvalidSymbolError(
                _INVALID_SYMBOL_MSG.format(symbol=raw)
            )
        parts = raw.split(self.SEPARATOR)
        if len(parts) != 2:
            raise InvalidSymbolError(
                _INVALID_SYMBOL_MSG.format(symbol=raw)
            )
        base, quote = parts
        if not base or not quote:
            raise InvalidSymbolError(
                _INVALID_SYMBOL_MSG.format(symbol=raw)
            )
        self._raw = raw
        self._base = base
        self._quote = quote

    @property
    def base(self) -> str:
        return self._base

    @property
    def quote(self) -> str:
        return self._quote

    @property
    def quote_currency(self) -> str:
        """Identical to ``.quote``; named for clarity in balance contexts."""
        return self._quote

    def __str__(self) -> str:
        return self._raw

    def __repr__(self) -> str:
        return f"Symbol({self._raw!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Symbol):
            return NotImplemented
        return self._raw == other._raw

    def __hash__(self) -> int:
        return hash(self._raw)
