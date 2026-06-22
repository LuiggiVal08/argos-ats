"""Order domain VOs: OrderSide, OrderType, CompositeOrder, OrderResult.

Per spec section 5 Historia 4: an order is a composite of a market
entry + stop loss + take profit (bracket order). The domain layer
models the logical structure; the infrastructure layer maps it to
the exchange API.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_LOSS_MARKET = "STOP_LOSS_MARKET"
    STOP_LOSS_LIMIT = "STOP_LOSS_LIMIT"
    TAKE_PROFIT_MARKET = "TAKE_PROFIT_MARKET"
    TAKE_PROFIT_LIMIT = "TAKE_PROFIT_LIMIT"


class OrderStatus(str, Enum):
    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True)
class CompositeOrder:
    symbol: str
    side: OrderSide
    entry_amount: Decimal
    entry_price: Decimal | None = None
    sl_price: Decimal | None = None
    tp_price: Decimal | None = None

    def __post_init__(self) -> None:
        if self.entry_amount <= 0:
            raise ValueError(f"entry_amount must be > 0, got {self.entry_amount}")


@dataclass(frozen=True)
class OrderResult:
    id: str
    symbol: str
    side: OrderSide
    type: OrderType
    filled_amount: Decimal
    avg_price: Decimal | None = None
    status: OrderStatus = OrderStatus.NEW
    client_order_id: str = ""
    sl_order_id: str | None = None
    tp_order_id: str | None = None
