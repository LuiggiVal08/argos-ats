from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from ..events.base_event import TruthEvent, TruthEventType
from ..events.control_event import ControlTruthEvent
from ..events.episode_event import EpisodeTruthEvent
from ..events.inference_event import InferenceTruthEvent
from ..events.tensor_event import TensorTruthEvent
from ..events.trade_event import TradeTruthEvent

_SCHEMA_PATH = Path(__file__).parent.parent / "db" / "schema.sql"


class TruthStore:
    """Append-only SQLite event store — canonical record of ALL system events.

    Invariants:
        1. Append-only: NO UPDATE, NO DELETE, NO OVERWRITE
        2. Hash chaining: every event links cryptographically to its predecessor
        3. Deterministic replay: replay(events[0:n]) === original execution
    """

    def __init__(self, db_path: str = "") -> None:
        self._path = db_path or os.environ.get(
            "ARGOS_TRUTH_DB",
            str(Path.cwd() / "forward_test" / "truth.db"),
        )
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    # ── Connection management ──────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self._path)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
        return self._conn

    def _init_db(self) -> None:
        path = Path(self._path)
        path.parent.mkdir(parents=True, exist_ok=True)
        schema = _SCHEMA_PATH.read_text() if _SCHEMA_PATH.exists() else ""
        if schema:
            self._connect().executescript(schema)
            self._conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @property
    def db_path(self) -> str:
        return self._path

    # ── Hash chain ─────────────────────────────────────────────────

    def _get_last_hash(self) -> str:
        """Return state_hash of the most recent event, or '' if none."""
        cur = self._connect().execute(
            "SELECT state_hash FROM events ORDER BY id DESC LIMIT 1"
        )
        row = cur.fetchone()
        return row["state_hash"] if row else ""

    # ── Append events ──────────────────────────────────────────────

    def append_event(self, event: TruthEvent) -> int:
        """Append a single event to the truth store. Returns event id."""
        parent_hash = self._get_last_hash()
        chained = event.with_chain(parent_hash)
        row = chained.to_db_row()
        cur = self._connect().execute(
            """INSERT INTO events (timestamp, run_id, symbol, event_type,
                                   payload, state_hash, parent_hash)
               VALUES (:timestamp, :run_id, :symbol, :event_type,
                       :payload, :state_hash, :parent_hash)""",
            row,
        )
        self._conn.commit()
        return cur.lastrowid or 0

    def append_trade(
        self,
        run_id: str,
        symbol: str,
        entry_ts: str,
        exit_ts: str,
        side: str,
        entry_price: float,
        exit_price: float,
        size: float,
        gross_pnl: float,
        costs: float,
        net_pnl: float,
        duration_bars: int,
        exit_reason: str,
    ) -> int:
        event = TradeTruthEvent(
            run_id=run_id,
            symbol=symbol,
            entry_ts=entry_ts,
            exit_ts=exit_ts,
            side=side,
            entry_price=entry_price,
            exit_price=exit_price,
            size=size,
            gross_pnl=gross_pnl,
            costs=costs,
            net_pnl=net_pnl,
            duration_bars=duration_bars,
            exit_reason=exit_reason,
        )
        return self.append_event(event.to_truth_event())

    def append_episode(
        self,
        run_id: str,
        symbol: str,
        episode_id: str,
        t_entry: str,
        t_exit: str | None = None,
        side: str = "",
        pnl: float = 0.0,
        regime_at_entry: str = "",
        event_subtype: str = "created",
    ) -> int:
        event = EpisodeTruthEvent(
            run_id=run_id,
            symbol=symbol,
            episode_id=episode_id,
            t_entry=t_entry,
            t_exit=t_exit,
            side=side,
            pnl=pnl,
            regime_at_entry=regime_at_entry,
            event_subtype=event_subtype,
        )
        return self.append_event(event.to_truth_event())

    def append_tensor(
        self,
        run_id: str,
        symbol: str,
        n_trades: int,
        directional_mean: float,
        directional_identifiable: bool,
        timing_mean: float,
        timing_identifiable: bool,
        execution_mean: float,
        execution_identifiable: bool,
        structural_mean: float,
        structural_identifiable: bool,
        timestamp: str = "",
    ) -> int:
        event = TensorTruthEvent(
            run_id=run_id,
            symbol=symbol,
            n_trades=n_trades,
            directional_mean=directional_mean,
            directional_identifiable=directional_identifiable,
            timing_mean=timing_mean,
            timing_identifiable=timing_identifiable,
            execution_mean=execution_mean,
            execution_identifiable=execution_identifiable,
            structural_mean=structural_mean,
            structural_identifiable=structural_identifiable,
            timestamp=timestamp,
        )
        return self.append_event(event.to_truth_event())

    def append_inference(
        self,
        run_id: str,
        symbol: str,
        p_edge_absolute: float,
        p_edge_relative: float,
        p_failure: float,
        utility_model: float,
        utility_h0_median: float,
        eia_passed: bool,
        eia_separability: bool,
        eia_null_invariance: bool,
        eia_representation_stability: bool,
        n_trades: int,
        n_bars: int,
        timestamp: str = "",
    ) -> int:
        event = InferenceTruthEvent(
            run_id=run_id,
            symbol=symbol,
            p_edge_absolute=p_edge_absolute,
            p_edge_relative=p_edge_relative,
            p_failure=p_failure,
            utility_model=utility_model,
            utility_h0_median=utility_h0_median,
            eia_passed=eia_passed,
            eia_separability=eia_separability,
            eia_null_invariance=eia_null_invariance,
            eia_representation_stability=eia_representation_stability,
            n_trades=n_trades,
            n_bars=n_bars,
            timestamp=timestamp,
        )
        return self.append_event(event.to_truth_event())

    def append_control(
        self,
        run_id: str,
        symbol: str,
        action: str,
        h_interno: float,
        h_externo: float,
        hazard_immediate: float,
        hazard_cumulative: float,
        reason: str,
        days_in_reduce: int,
        timestamp: str = "",
    ) -> int:
        event = ControlTruthEvent(
            run_id=run_id,
            symbol=symbol,
            action=action,
            h_interno=h_interno,
            h_externo=h_externo,
            hazard_immediate=hazard_immediate,
            hazard_cumulative=hazard_cumulative,
            reason=reason,
            days_in_reduce=days_in_reduce,
            timestamp=timestamp,
        )
        return self.append_event(event.to_truth_event())

    # ── Query ──────────────────────────────────────────────────────

    def get_events(
        self,
        run_id: str = "",
        symbol: str = "",
        event_type: str = "",
        limit: int = 0,
        offset: int = 0,
    ) -> list[TruthEvent]:
        conn = self._connect()
        parts: list[str] = []
        params: dict[str, Any] = {}

        if run_id:
            parts.append("run_id = :run_id")
            params["run_id"] = run_id
        if symbol:
            parts.append("symbol = :symbol")
            params["symbol"] = symbol
        if event_type:
            parts.append("event_type = :event_type")
            params["event_type"] = event_type

        where = " AND ".join(parts) if parts else "1=1"
        sql = f"SELECT * FROM events WHERE {where} ORDER BY id ASC"
        if limit > 0:
            sql += f" LIMIT {limit} OFFSET {offset}"

        rows = conn.execute(sql, params).fetchall()
        return [TruthEvent.from_db_row(dict(r)) for r in rows]

    def get_by_type(
        self,
        event_type: TruthEventType,
        run_id: str = "",
        symbol: str = "",
    ) -> list[TruthEvent]:
        return self.get_events(
            run_id=run_id,
            symbol=symbol,
            event_type=event_type.value,
        )

    def count(self) -> int:
        row = self._connect().execute("SELECT COUNT(*) as cnt FROM events").fetchone()
        return row["cnt"] if row else 0

    def verify_hash_chain(self, run_id: str = "") -> dict[str, Any]:
        """Verify cryptographic chain integrity.

        Returns dict with:
        - valid: bool — True if all hashes match
        - checked: int — number of events checked
        - failures: list — details of mismatches
        """
        events = self.get_events(run_id=run_id) if run_id else self.get_events()
        failures: list[dict[str, Any]] = []
        expected_parent = ""

        for ev in events:
            computed = ev.compute_hash(expected_parent)
            if computed != ev.state_hash:
                failures.append({
                    "id": ev.event_id,
                    "event_type": ev.event_type.value,
                    "timestamp": ev.timestamp,
                    "expected_hash": computed,
                    "stored_hash": ev.state_hash,
                })
            if ev.parent_hash != expected_parent:
                failures.append({
                    "id": ev.event_id,
                    "type": "parent_hash_mismatch",
                    "expected_parent": expected_parent,
                    "stored_parent": ev.parent_hash,
                })
            expected_parent = ev.state_hash

        return {
            "valid": len(failures) == 0,
            "checked": len(events),
            "failures": failures,
        }

    def replay(
        self,
        run_id: str = "",
        symbol: str = "",
    ) -> dict[str, Any]:
        """Reconstruct system state from events.

        Returns a dict with all reconstructed timelines.
        """
        events = self.get_events(run_id=run_id, symbol=symbol)

        state: dict[str, Any] = {
            "trades": [],
            "episodes": [],
            "episodes_open": {},
            "tensors": [],
            "inferences": [],
            "control_decisions": [],
            "last_control_action": None,
            "last_control_reason": "",
            "n_events": len(events),
            "hash_valid": True,
        }

        for ev in events:
            p = ev.payload
            if ev.event_type == TruthEventType.TRADE:
                state["trades"].append(p)
            elif ev.event_type == TruthEventType.EPISODE:
                ep_id = p.get("episode_id", "")
                if p.get("event_subtype") == "created":
                    state["episodes_open"][ep_id] = p
                else:
                    state["episodes_open"].pop(ep_id, None)
                    state["episodes"].append(p)
            elif ev.event_type == TruthEventType.TENSOR:
                state["tensors"].append(p)
            elif ev.event_type == TruthEventType.INFERENCE:
                state["inferences"].append(p)
            elif ev.event_type == TruthEventType.CONTROL:
                state["control_decisions"].append(p)
                state["last_control_action"] = p.get("action")
                state["last_control_reason"] = p.get("reason", "")

        return state

    def __enter__(self) -> TruthStore:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
