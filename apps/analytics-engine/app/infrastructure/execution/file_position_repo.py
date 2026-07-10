"""FilePositionRepository — persiste posiciones en JSON.

Para recuperación ante reinicios del bot. Mismo patrón que
FileBacktestReporter (H8) pero con lectura/escritura bidireccional.

NOTE: Production uses SQLitePositionRepository. This file is used
for tests and development only.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from ...domain.value_objects.live_position import LivePosition
from ...domain.value_objects.order import OrderSide
from ...application.ports.position_repository import PositionRepository


class _PositionEncoder(json.JSONEncoder):
    def default(self, o: Any) -> Any:
        if isinstance(o, Decimal):
            return float(o)
        if isinstance(o, datetime):
            return o.isoformat()
        if isinstance(o, OrderSide):
            return o.value
        return super().default(o)


class FilePositionRepository:
    """Persiste posiciones en un archivo JSON.

    Args:
        file_path: Ruta al archivo JSON (default: data/positions.json).
    """

    def __init__(self, file_path: str = "data/positions.json") -> None:
        self._file_path = file_path
        self._lock = asyncio.Lock()
        self._ensure_dir()

    def _ensure_dir(self) -> None:
        Path(self._file_path).parent.mkdir(parents=True, exist_ok=True)

    def _load_all(self) -> dict[str, dict]:
        if not os.path.exists(self._file_path):
            return {}
        with open(self._file_path) as f:
            return json.load(f)

    def _save_all(self, data: dict[str, dict]) -> None:
        with open(self._file_path, "w") as f:
            json.dump(data, f, cls=_PositionEncoder, indent=2)

    async def _atomic_update(self, fn: Callable[[dict[str, dict]], dict[str, dict]]) -> dict[str, dict]:
        """Execute fn inside a lock, passing the current state and saving the result."""
        async with self._lock:
            data = self._load_all()
            data = fn(data)
            self._save_all(data)
            return data

    def _dict_to_position(self, d: dict) -> LivePosition:
        return LivePosition(
            position_id=d["position_id"],
            symbol=d["symbol"],
            side=OrderSide(d["side"]),
            units=Decimal(str(d["units"])),
            entry_price=Decimal(str(d["entry_price"])),
            current_price=Decimal(str(d["current_price"])),
            sl_price=Decimal(str(d["sl_price"])) if d.get("sl_price") else None,
            tp_price=Decimal(str(d["tp_price"])) if d.get("tp_price") else None,
            unrealized_pnl=Decimal(str(d.get("unrealized_pnl", 0))),
            opened_at=datetime.fromisoformat(d["opened_at"]),
            closed_at=datetime.fromisoformat(d["closed_at"]) if d.get("closed_at") else None,
            realized_pnl=Decimal(str(d["realized_pnl"])) if d.get("realized_pnl") else None,
            status=d.get("status", "OPEN"),
            metadata=d.get("metadata", {}),
        )

    async def save(self, position: LivePosition) -> None:
        async def _upsert(data: dict[str, dict]) -> dict[str, dict]:
            data[position.position_id] = {
                "position_id": position.position_id,
                "symbol": position.symbol,
                "side": position.side.value,
                "units": position.units,
                "entry_price": position.entry_price,
                "current_price": position.current_price,
                "sl_price": position.sl_price,
                "tp_price": position.tp_price,
                "unrealized_pnl": position.unrealized_pnl,
                "opened_at": position.opened_at,
                "closed_at": position.closed_at,
                "realized_pnl": position.realized_pnl,
                "status": position.status,
                "metadata": position.metadata,
            }
            return data
        await self._atomic_update(_upsert)

    async def load(self, position_id: str) -> LivePosition | None:
        data = self._load_all()
        d = data.get(position_id)
        return self._dict_to_position(d) if d else None

    async def list_open(self) -> list[LivePosition]:
        data = self._load_all()
        return [
            self._dict_to_position(d)
            for d in data.values()
            if d.get("status") == "OPEN"
        ]

    async def list_all(self) -> list[LivePosition]:
        data = self._load_all()
        return [self._dict_to_position(d) for d in data.values()]

    async def delete(self, position_id: str) -> bool:
        async with self._lock:
            data = self._load_all()
            if position_id not in data:
                return False
            del data[position_id]
            self._save_all(data)
            return True
