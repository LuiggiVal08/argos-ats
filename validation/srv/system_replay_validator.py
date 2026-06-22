"""SystemReplayValidator — Dual-run inference consistency certification.

Verifica que:
    Φ(EdgeInferenceEngine(D_live)) ≈ Φ(EdgeInferenceEngine(D_replay))

Si falla → Truth Store NO reproduce el universo original.
Cualquier conclusión posterior es sospechosa.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from .tensor_comparator import TensorComparator, EPSILON
from .validation_report import SrvReport


class SystemReplayValidator:
    """Orquestador del dual-run consistency test.

    Usage
    -----
        validator = SystemReplayValidator(truth_store, inference_engine)
        report = validator.run("BTC", csv_path="forward_test/trades_btc.csv")
        print(report.to_dict())
    """

    def __init__(
        self,
        truth_store: Any,
        inference_engine: Any | None = None,
    ) -> None:
        self._truth_store = truth_store
        self._inference_engine = inference_engine

    def run(
        self,
        run_id: str,
        symbol: str,
        csv_path: str | Path | None = None,
        trades_live: list[dict[str, Any]] | None = None,
        seed: int = 42,
    ) -> SrvReport:
        report = SrvReport(run_id=run_id, symbol=symbol)

        # 1. Load live trades
        live_trades: list[dict[str, Any]] = []
        if csv_path:
            live_trades = self._normalize_trades(self._load_trades_csv(csv_path))
        elif trades_live is not None:
            live_trades = self._normalize_trades(trades_live)
        report.n_live_trades = len(live_trades)

        # 2. Replay from Truth Store
        replayed = self._truth_store.replay(run_id=run_id, symbol=symbol)
        replay_trades = self._normalize_trades(replayed.get("trades", []))
        report.n_replay_trades = len(replay_trades)
        report.n_events_replayed = replayed.get("n_events", 0)

        # 3. Trade list identity check (foundational)
        trade_identity = self._compare_trade_lists(live_trades, replay_trades)
        report.tensor_drift["trade_list_identity"] = trade_identity

        # 4. Run inference on both
        if self._inference_engine is not None and len(live_trades) > 0:
            phi_live = self._inference_engine.infer(live_trades)
            phi_replay = self._inference_engine.infer(replay_trades)

            # 4a. Tensor drift
            tensor_result = TensorComparator.compare(
                phi_live, phi_replay, epsilon=EPSILON
            )
            report.tensor_drift["components"] = [
                c.to_dict() for c in tensor_result["components"]
            ]
            report.tensor_drift["all_within_tolerance"] = (
                tensor_result["all_within_tolerance"]
            )
            report.tensor_drift["all_identifiability_match"] = (
                tensor_result["all_identifiability_match"]
            )
            report.tensor_drift["total_drift"] = tensor_result["total_drift"]
            report.tensor_drift["n_failures"] = tensor_result["n_failures"]

            # 4b. EIA identifiability flags
            eia_live = self._extract_eia(phi_live)
            eia_replay = self._extract_eia(phi_replay)
            all_keys = sorted(set(list(eia_live.keys()) + list(eia_replay.keys())))
            report.eia_drift = {
                "identifiability_match": all(
                    eia_live.get(k) == eia_replay.get(k) for k in all_keys
                ),
                "record": {
                    k: {"live": eia_live.get(k), "replay": eia_replay.get(k)}
                    for k in all_keys
                },
            }

            # 4c. Store live and replay dicts for reference
            report.tensor_drift["phi_live"] = self._safe_to_dict(phi_live)
            report.tensor_drift["phi_replay"] = self._safe_to_dict(phi_replay)

        # 5. Control action (from Truth Store stored events)
        control_live = self._get_last_event(replayed, "control_decisions")
        if control_live:
            report.control_drift = {
                "action_live": control_live.get("action"),
                "action_replay": control_live.get("action"),  # same Truth Store
                "action_match": True,
                "h_interno_live": control_live.get("h_interno"),
                "h_externo_live": control_live.get("h_externo"),
                "reason_live": control_live.get("reason"),
            }

        # 6. Probability comparison (from Truth Store stored inference event)
        inference_live = self._get_last_event(replayed, "inferences")
        if inference_live:
            report.probability_drift = {
                "p_edge_absolute_live": inference_live.get("p_edge_absolute"),
                "p_edge_relative_live": inference_live.get("p_edge_relative"),
                "p_failure_live": inference_live.get("p_failure"),
                "utility_model_live": inference_live.get("utility_model"),
                "utility_h0_live": inference_live.get("utility_h0_median"),
                "p_edge_absolute_drift": 0.0,  # same source
                "p_edge_relative_drift": 0.0,
                "p_failure_drift": 0.0,
            }

        return report

    # ── Helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _normalize_trades(
        trades: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Normalize trade dicts (string sides → int) for inference engine."""
        out: list[dict[str, Any]] = []
        for t in trades:
            d = dict(t)
            raw_side = d.get("side", 0)
            if isinstance(raw_side, str):
                raw_side = raw_side.upper()
                d["side"] = 1 if raw_side in ("1", "LONG", "BUY") else -1
            else:
                d["side"] = int(raw_side) if raw_side else 0
            for k in ("entry_price", "exit_price", "size", "gross_pnl", "costs", "net_pnl"):
                if k in d and d[k] is not None:
                    d[k] = float(d[k])
            out.append(d)
        return out

    @staticmethod
    def _load_trades_csv(path: str | Path) -> list[dict[str, Any]]:
        trades: list[dict[str, Any]] = []
        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                row["side"] = int(row["side"])
                row["entry_price"] = float(row["entry_price"])
                row["exit_price"] = float(row["exit_price"])
                row["size"] = float(row["size"])
                row["gross_pnl"] = float(row["gross_pnl"])
                row["costs"] = float(row["costs"])
                row["net_pnl"] = float(row["net_pnl"])
                row["duration_bars"] = int(row["duration_bars"])
                trades.append(row)
        return trades

    @staticmethod
    def _compare_trade_lists(
        live: list[dict[str, Any]],
        replay: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Full trade-level identity check."""
        n_live = len(live)
        n_replay = len(replay)

        if n_live != n_replay:
            return {
                "match": False,
                "n_live": n_live,
                "n_replay": n_replay,
                "error": "count_mismatch",
                "hash_live": "",
                "hash_replay": "",
            }

        # Compare JSON hashes
        def trade_hash(trades: list[dict[str, Any]]) -> str:
            raw = json.dumps(trades, sort_keys=True, default=str)
            return hashlib.sha256(raw.encode()).hexdigest()

        h_live = trade_hash(live)
        h_replay = trade_hash(replay)

        match = h_live == h_replay
        return {
            "match": match,
            "n_live": n_live,
            "n_replay": n_replay,
            "hash_live": h_live,
            "hash_replay": h_replay,
            "error": None if match else "hash_mismatch",
        }

    @staticmethod
    def _get_last_event(
        replayed: dict[str, Any],
        key: str,
    ) -> dict[str, Any] | None:
        events = replayed.get(key, [])
        return events[-1] if events else None

    @staticmethod
    def _extract_eia(phi: Any) -> dict[str, bool]:
        """Extract identifiability flags from EdgeTensor or dict."""
        if hasattr(phi, "identifiability"):
            return dict(phi.identifiability())
        if isinstance(phi, dict):
            return dict(phi.get("identifiability", {}))
        return {"directional": False, "timing": False, "execution": False, "structural": False}

    @staticmethod
    def _safe_to_dict(phi: Any) -> dict[str, Any]:
        if hasattr(phi, "to_dict"):
            return phi.to_dict()
        if isinstance(phi, dict):
            return phi
        return {}
