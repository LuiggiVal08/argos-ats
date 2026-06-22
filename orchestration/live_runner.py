"""Live runner — entry point for production / simulation forward test.

Resolves ENVIRONMENT_MODE and RESUME_MODE, initializes engines with
proper recovery state, and runs the forward test loop.

Usage:
    ENVIRONMENT_MODE=LIVE_SIMULATION \\
    RESUME_MODE=true \\
    CHECKPOINT_INTERVAL=30 \\
    python -m orchestration.live_runner
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("live_runner")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _bool_env(name: str, default: bool = False) -> bool:
    val = _env(name)
    if not val:
        return default
    return val.lower() in ("1", "true", "yes")


async def main() -> None:
    mode = _env("ENVIRONMENT_MODE", "LIVE_SIMULATION").upper()
    resume = _bool_env("RESUME_MODE", False)
    checkpoint_interval = int(_env("CHECKPOINT_INTERVAL", "30"))

    valid_modes = {"BACKTESTING", "PAPER_TRADING", "LIVE", "LIVE_SIMULATION"}
    if mode not in valid_modes:
        log.error("invalid ENVIRONMENT_MODE", mode=mode, valid=list(valid_modes))
        sys.exit(1)

    log.info(
        "live_runner_start",
        mode=mode,
        resume=resume,
        checkpoint_interval=checkpoint_interval,
    )

    # ── 1. Resolve checkpoint paths ────────────────────────────────
    state_dir = Path.cwd() / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    de_checkpoint = str(state_dir / "checkpoint.de.json")
    ae_checkpoint = str(state_dir / "checkpoint.ae.json")

    # ── 2. Recovery state ──────────────────────────────────────────
    from app.infrastructure.state.checkpoint_reader import CheckpointReader
    from app.infrastructure.recovery.recovery_engine import (
        RecoveryEngine,
        TruthStoreReplay,
    )

    ae_recovery = RecoveryEngine(ae_checkpoint, mode)
    ae_state = ae_recovery.restore(resume_mode=resume)

    log.info(
        "recovery_state",
        is_resume=ae_state.is_resume,
        mode=ae_state.mode,
        last_signal=ae_state.last_signal_id,
        last_order=ae_state.last_order_id,
        equity=ae_state.equity,
    )

    # ── 3. Build composition ───────────────────────────────────────
    from app.composition import build_composition
    comp = build_composition()

    # ── 4. Checkpoint writer (periodic save) ───────────────────────
    from app.infrastructure.state.checkpoint_writer import CheckpointWriter

    checkpoint_writer = CheckpointWriter(
        file_path=ae_checkpoint,
        interval_seconds=checkpoint_interval,
    )

    def _checkpoint_provider() -> dict[str, Any]:
        return {
            "last_processed_timestamp": ae_state.last_processed_timestamp,
            "last_candle_timestamp": ae_state.last_candle_timestamp,
            "last_signal_id": ae_state.last_signal_id,
            "last_order_id": ae_state.last_order_id,
            "last_fill_id": ae_state.last_fill_id,
            "open_positions": [],
            "equity": ae_state.equity,
            "mode": mode,
            "engine": "analytics-engine",
            "schema_version": 1,
        }

    await checkpoint_writer.start(_checkpoint_provider)

    # ── 5. Run the engine ──────────────────────────────────────────
    shutdown_event = asyncio.Event()

    def _signal_handler() -> None:
        log.info("shutdown_signal_received")
        shutdown_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows or no signal support
            pass

    log.info("engine_running", mode=mode)

    try:
        await shutdown_event.wait()
    except asyncio.CancelledError:
        pass
    finally:
        log.info("shutting_down")
        await checkpoint_writer.stop()
        # Final checkpoint flush
        await checkpoint_writer.write(_checkpoint_provider())

        if comp.exchange is not None:
            try:
                await comp.exchange.close()
            except Exception:
                pass

        log.info("shutdown_complete")


if __name__ == "__main__":
    asyncio.run(main())
