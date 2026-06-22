"""ReplayEngine — orquestador principal de replay determinista.

Reconstruye exactamente cualquier ejecución del forward test a partir
del ledger persistido, sin acceso al modelo en vivo ni al inference engine.

Reglas (spec):
- NO genera nuevas decisiones del modelo
- NO recalcula señales del forward model
- NO introduce stochasticity nueva
- Solo reconstruye o valida consistencia del pasado
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ..edl.inference import EdgeTensor

from .event_stream import Event, EventStream, EventType
from .reconstruction import (
    ControlRebuilder,
    EpisodeRebuilder,
    TensorRebuilder,
    TradeRebuilder,
)
from .replay_state import ReplayPosition, ReplayState
from .validation import DriftDetector, ReplayConsistencyReport


class ReplayEngine:
    """Deterministic replay of forward test experiments.

    Usage
    -----
        engine = ReplayEngine(trade_repo, episode_repo, tensor_repo)
        engine.load_experiment("BTC")
        result = engine.replay(mode="full")
        report = engine.validate()
    """

    def __init__(
        self,
        trade_repo: Any | None = None,
        episode_repo: Any | None = None,
        tensor_repo: Any | None = None,
    ) -> None:
        self._trade_repo = trade_repo
        self._episode_repo = episode_repo
        self._tensor_repo = tensor_repo

        self._trade_rebuilder = TradeRebuilder()
        self._episode_rebuilder = EpisodeRebuilder()
        self._tensor_rebuilder = TensorRebuilder()
        self._control_rebuilder = ControlRebuilder()

        self._event_stream = EventStream()
        self._state = ReplayState()
        self._consistency = ReplayConsistencyReport()
        self._drift_detector = DriftDetector()

        # Original data for comparison
        self._original_protocol: dict[str, Any] | None = None
        self._original_state: dict[str, Any] | None = None
        self._original_trades_csv: list[dict[str, Any]] = []

    def load_experiment(self, experiment_id: str) -> None:
        """Load all ledger data for an experiment.

        Consumes from:
        - TradeRepository.replay() → list[TradeDTO]
        - EpisodeRepository.replay() → list[EpisodeDTO]
        - TensorRepository.replay() → list[EdgeTensorDTO]
        - forward_test CSV/JSON snapshots (optional, for validation)
        """
        self._state.symbol = experiment_id.upper()
        self._state.experiment_id = experiment_id
        self._event_stream.clear()

        # Load from repositories if available
        trades: list[Any] = []
        episodes: list[Any] = []
        tensors: list[Any] = []

        if self._trade_repo is not None:
            try:
                trades = self._trade_repo.replay()
            except Exception:
                trades = []

        if self._episode_repo is not None:
            try:
                episodes = self._episode_repo.replay()
            except Exception:
                episodes = []

        if self._tensor_repo is not None:
            try:
                tensors = self._tensor_repo.replay()
            except Exception:
                tensors = []

        # Rebuild from DTOs
        self._trade_rebuilder.rebuild(trades)
        self._episode_rebuilder.rebuild(episodes)
        self._tensor_rebuilder.rebuild(tensors)

        # Build event stream from trades
        for i, trade in enumerate(trades):
            self._event_stream.add_event(
                EventType.TRADE_CLOSED,
                data={
                    "symbol": trade.symbol,
                    "entry_ts": trade.entry_ts,
                    "exit_ts": trade.exit_ts,
                    "side": trade.side,
                    "net_pnl": trade.net_pnl,
                    "costs": trade.costs,
                },
                timestamp=trade.exit_ts,
            )

        # Build event stream from episodes
        for ep in episodes:
            event_type = (
                EventType.EPISODE_CREATED
                if ep.t_exit is None
                else EventType.EPISODE_SETTLED
            )
            self._event_stream.add_event(
                event_type,
                data={
                    "episode_id": ep.episode_id,
                    "side": ep.side,
                    "pnl": ep.pnl,
                    "regime": ep.regime_at_entry,
                    "feature_hash": ep.feature_hash,
                },
                timestamp=ep.t_entry,
            )

        # Build event stream from tensors
        for tensor in tensors:
            self._event_stream.add_event(
                EventType.TENSOR_STORED,
                data={
                    "symbol": tensor.symbol,
                    "n_trades": tensor.n_trades,
                    "directional_mean": tensor.directional.mean,
                    "timing_mean": tensor.timing.mean,
                    "execution_mean": tensor.execution.mean,
                    "structural_mean": tensor.structural.mean,
                },
                timestamp=tensor.timestamp,
            )

    def load_snapshot(
        self,
        protocol_path: str | Path | None = None,
        state_path: str | Path | None = None,
        trades_csv_path: str | Path | None = None,
    ) -> None:
        """Load original snapshot files for validation.

        These are optional — they provide the "ground truth" that
        the replay validates against.
        """
        if protocol_path:
            path = Path(protocol_path)
            if path.exists():
                with open(path) as f:
                    self._original_protocol = json.load(f)

                # Add control events
                control = self._original_protocol.get("control", {})
                self._event_stream.add_event(
                    EventType.CONTROL_DECISION,
                    data={
                        "action": control.get("action", "UNKNOWN"),
                        "h_interno": control.get("h_interno", 0.0),
                        "h_externo": control.get("h_externo", 0.0),
                        "reason": control.get("reason", ""),
                    },
                    timestamp=self._original_protocol.get("timestamp", ""),
                )

                # Rebuild control decisions
                self._control_rebuilder.rebuild([self._original_protocol])

        if state_path:
            path = Path(state_path)
            if path.exists():
                with open(path) as f:
                    self._original_state = json.load(f)

        if trades_csv_path:
            path = Path(trades_csv_path)
            if path.exists():
                import csv
                with open(path) as f:
                    reader = csv.DictReader(f)
                    self._original_trades_csv = list(reader)

    def replay(self, mode: str = "full") -> dict[str, Any]:
        """Execute deterministic replay of the experiment.

        Parameters
        ----------
        mode : str
            - "full": reconstructs the entire pipeline
            - "control": only control layer decisions
            - "inference": only Φ + H₀ tensor data

        Returns
        -------
        dict
            Reconstructed system state summary.
        """
        if mode not in ("full", "control", "inference"):
            raise ValueError(f"Unknown replay mode: {mode}")

        self._state.mode = mode

        # Update state from event stream
        for event in self._event_stream:
            self._apply_event(event)
            if mode == "control" and event.event_type != EventType.CONTROL_DECISION:
                continue
            if mode == "inference" and event.event_type != EventType.TENSOR_STORED:
                continue

        self._state.total_bars = max(
            self._original_state.get("last_bar_idx", 0)
            if self._original_state else 0,
            len(self._original_trades_csv) if self._original_trades_csv else 0,
        )

        return self.build_report()

    def _apply_event(self, event: Event) -> None:
        """Update replay state from a single event."""
        data = event.data or {}

        if event.event_type == EventType.TRADE_CLOSED:
            self._state.total_trades += 1
            self._state.total_costs += data.get("costs", 0.0)
            self._state.last_trade = data

        elif event.event_type == EventType.EPISODE_SETTLED:
            self._state.last_episode = data

        elif event.event_type == EventType.TENSOR_STORED:
            self._state.last_tensor = data

        elif event.event_type == EventType.CONTROL_DECISION:
            self._state.last_control = data

    def build_report(self) -> dict[str, Any]:
        """Build a comprehensive replay report."""
        return {
            "experiment_id": self._state.experiment_id,
            "symbol": self._state.symbol,
            "mode": self._state.mode,
            "state": self._state.to_dict(),
            "events": {
                "total": self._event_stream.count,
                "trades": len(self._event_stream.by_type(EventType.TRADE_CLOSED)),
                "episodes": (
                    len(self._event_stream.by_type(EventType.EPISODE_CREATED))
                    + len(self._event_stream.by_type(EventType.EPISODE_SETTLED))
                ),
                "tensors": len(self._event_stream.by_type(EventType.TENSOR_STORED)),
                "control_decisions": len(
                    self._event_stream.by_type(EventType.CONTROL_DECISION)
                ),
            },
            "reconstructed": {
                "n_trades": len(self._trade_rebuilder.trades),
                "n_episodes": len(self._episode_rebuilder.all_episodes),
                "n_tensors": len(self._tensor_rebuilder.tensors),
                "n_control_decisions": len(self._control_rebuilder.decisions),
                "last_control_action": (
                    self._control_rebuilder.last_decision.get("action")
                    if self._control_rebuilder.last_decision else None
                ),
            },
        }

    def validate(self) -> dict[str, Any]:
        """Compare replay vs original forward_test.

        Returns consistency metrics and drift detection.
        """
        self._consistency = ReplayConsistencyReport()

        # Trade consistency
        trade_result = self._trade_rebuilder.validate_consistency(
            self._original_trades_csv
        )
        self._consistency.add_result("trades", trade_result)

        # Episode consistency
        episode_result = self._episode_rebuilder.validate_consistency(
            self._original_state
        )
        self._consistency.add_result("episodes", episode_result)

        # Tensor consistency
        tensor_result = self._tensor_rebuilder.validate_consistency(
            self._original_protocol
        )
        self._consistency.add_result("tensors", tensor_result)

        # Control consistency (only if original protocol loaded)
        control_result = self._control_rebuilder.validate_consistency(
            self._original_protocol
        )
        self._consistency.add_result("control", control_result)

        report = self._consistency.summary()

        # Drift detection
        if self._original_protocol:
            drift_results = {
                "utility": self._drift_detector.check_utility_drift(
                    self._original_protocol.get("edge", {}).get("utility_model"),
                    None,  # reconstructed utility not available from DTOs
                ),
                "tensor": self._drift_detector.check_tensor_drift(
                    self._original_protocol,
                    (self._tensor_rebuilder.latest
                     and self._tensor_rebuilder.latest.to_dict()
                     if hasattr(self._tensor_rebuilder.latest, "to_dict")
                     else None),
                ),
                "control": self._drift_detector.check_control_drift(
                    self._original_protocol.get("control"),
                    (self._control_rebuilder.last_decision
                     if self._control_rebuilder.last_decision else None),
                ),
            }
            report["drift_analysis"] = drift_results

        report["state"] = self._state.to_dict()
        return report

    @property
    def event_stream(self) -> EventStream:
        return self._event_stream

    @property
    def state(self) -> ReplayState:
        return self._state
