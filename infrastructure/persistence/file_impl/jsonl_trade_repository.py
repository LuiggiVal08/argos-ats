from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Generator

from infrastructure.persistence.dto.trade_dto import TradeDTO


class JsonlTradeRepository:
    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def append_trade(self, trade: TradeDTO) -> None:
        record = {
            "type": "trade",
            "symbol": trade.symbol,
            "entry_ts": trade.entry_ts,
            "exit_ts": trade.exit_ts,
            "side": trade.side,
            "entry_price": trade.entry_price,
            "exit_price": trade.exit_price,
            "size": trade.size,
            "gross_pnl": trade.gross_pnl,
            "costs": trade.costs,
            "net_pnl": trade.net_pnl,
            "duration_bars": trade.duration_bars,
            "exit_reason": trade.exit_reason,
            "experiment_id": trade.experiment_id,
        }
        self._atomic_append(record)

    def get_trades(self, symbol: str, start: datetime, end: datetime) -> list[TradeDTO]:
        result: list[TradeDTO] = []
        for record in self._iter_records():
            if record.get("symbol") != symbol:
                continue
            ts = datetime.fromisoformat(record.get("entry_ts", ""))
            if ts < start or ts > end:
                continue
            result.append(self._record_to_dto(record))
        return result

    def replay(self) -> list[TradeDTO]:
        return [self._record_to_dto(r) for r in self._iter_records()]

    def _iter_records(self) -> Generator[dict, None, None]:
        if not self._path.exists():
            return
        with open(self._path) as f:
            for line in f:
                line = line.strip()
                if line:
                    yield json.loads(line)

    def _record_to_dto(self, record: dict) -> TradeDTO:
        return TradeDTO(
            symbol=record.get("symbol", ""),
            entry_ts=record.get("entry_ts", ""),
            exit_ts=record.get("exit_ts", ""),
            side=record.get("side", ""),
            entry_price=record.get("entry_price", 0.0),
            exit_price=record.get("exit_price", 0.0),
            size=record.get("size", 0.0),
            gross_pnl=record.get("gross_pnl", 0.0),
            costs=record.get("costs", 0.0),
            net_pnl=record.get("net_pnl", 0.0),
            duration_bars=record.get("duration_bars", 0),
            exit_reason=record.get("exit_reason", ""),
            experiment_id=record.get("experiment_id", ""),
        )

    def _atomic_append(self, record: dict) -> None:
        line = json.dumps(record, sort_keys=True) + "\n"
        if self._path.exists():
            with open(self._path, "a") as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())
        else:
            tmp = self._path.with_suffix(".tmp")
            with open(tmp, "w") as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())
            tmp.rename(self._path)
