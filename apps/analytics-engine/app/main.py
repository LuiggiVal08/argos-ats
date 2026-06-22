# mypy: disable-error-code="str-unpack,union-attr"
"""Argos analytics & IA engine entry point."""
from __future__ import annotations

import asyncio
import os
import time
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import structlog
from contextlib import asynccontextmanager

import redis.asyncio as redis
from fastapi import FastAPI

from .api import (
    backtest_router,
    circuit_breaker_router,
    dataset_router,
    execution_router,
    incident_router,
    model_router,
    notification_router,
    observability_router,
    order_router,
    risk_router,
    shadow_router,
    training_router,
)
from .composition import Composition, build_composition
if TYPE_CHECKING:
    from .application.use_cases.monitor_positions import MonitorPositionsUseCase

log = structlog.get_logger()
app = FastAPI(title="argos-analytics-engine", version="0.1.0")
app.include_router(risk_router)
app.include_router(circuit_breaker_router)
app.include_router(incident_router)
app.include_router(model_router)
app.include_router(order_router)
app.include_router(backtest_router)
app.include_router(dataset_router)
app.include_router(execution_router)
app.include_router(notification_router)
app.include_router(training_router)
app.include_router(shadow_router)
app.include_router(observability_router)
app.include_router(shadow_router)


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _boot_timestamp
    _boot_timestamp = time.monotonic()
    comp: Composition = build_composition()
    app.state.composition = comp
    log.info("composition_built", mode=comp.mode, has_exchange=comp.exchange is not None)
    if comp.mode == "LIVE_SIMULATION":
        log.info("[BOOT] MODE = LIVE_SIMULATION (ENFORCED)")

    # FASE 5: Validate expected Redis streams exist at boot
    if comp.mode != "BACKTESTING":
        try:
            from redis.asyncio import Redis as _RedisClient
            url = os.environ.get("ARGOS_BROKER_URL", "redis://localhost:6379")
            _boot_redis = _RedisClient.from_url(url)
            symbol = os.environ.get("SYMBOL", "BTC/USDT")
            tick_stream = f"ticks:{symbol.replace('/', '').lower()}"
            expected_streams = [
                tick_stream,
                "signals:trading",
                "orders:execution",
                "fills:execution",
            ]
            for s in expected_streams:
                try:
                    exists = await _boot_redis.exists(s)
                    if not exists:
                        log.warning(
                            "boot_stream_missing",
                            stream=s,
                            hint="verify DE is running and publishing to this stream",
                        )
                    else:
                        log.info("boot_stream_found", stream=s)
                except Exception:
                    log.warning("boot_stream_check_failed", stream=s)
            await _boot_redis.aclose()
        except Exception as e:
            log.warning("boot_stream_validation_error", error=str(e))

    # FASE 4: Recovery startup — reconstruct state from persistence
    recovery_blocked = False
    from .composition import run_recovery
    try:
        recovery_report = await run_recovery(comp)
        if recovery_report is not None:
            log.info(
                "recovery_result",
                gate_state=recovery_report.gate.state.value,
                snapshot_loaded=recovery_report.snapshot_loaded,
                actions=recovery_report.actions_taken,
            )
            # Fix 1 (nuevo): Open stream barrier after successful recovery
            if hasattr(comp, "stream_barrier") and comp.stream_barrier is not None:
                recovery_ts = recovery_report.timestamp.timestamp()
                comp.stream_barrier.open(recovery_timestamp=recovery_ts)
                log.info("stream_barrier_opened", recovery_ts=recovery_ts)
        else:
            log.info("recovery_skipped", mode=comp.mode)
    except Exception as e:
        log.critical("recovery_fatal", error=str(e))
        recovery_blocked = True

    streaming_tasks: list[asyncio.Task] = []
    redis_client: redis.Redis | None = None
    maintenance_tasks: list[asyncio.Task] = []

    # ECL: Experiment Control Plane
    ecl_snapshot: Any = None
    ecl_sharpe_guard: Any = None
    ecl_baseline_checker: Any = None
    ecl_health_aggregator: Any = None

    # EDL: Trade Episode Store (evidence layer)
    episode_store: Any = None

    if recovery_blocked:
        log.critical("recovery_blocked_streaming_disabled")
        comp.recovery_blocked = True
    elif comp.streaming is not None:

        # FASE 7: LIVE_SIMULATION validation check
        if comp.mode == "LIVE_SIMULATION":
            _validation_ok = True
            _checks: list[str] = []
            if comp.mode != "LIVE_SIMULATION":
                _validation_ok = False
                _checks.append("mode_mismatch")
            if hasattr(comp, "recovery_blocked") and comp.recovery_blocked:
                _validation_ok = False
                _checks.append("recovery_blocked")
            if _validation_ok:
                log.info("[BOOT] LIVE_SIMULATION VALIDATION PASSED")
            else:
                log.warning(
                    "[BOOT] LIVE_SIMULATION VALIDATION FAILED",
                    failed_checks=_checks,
                )

        symbol = os.environ.get("SYMBOL", "BTC/USDT")
        url = os.environ.get("ARGOS_BROKER_URL", "redis://localhost:6379")
        redis_client = redis.from_url(url)
        app.state.redis_client = redis_client

        # CandlePersister: Redis-backed persistence + CCXT seed on cold boot
        from .infrastructure.trading.candle_persister import CandlePersister
        candle_persister = CandlePersister(redis_client, symbol, timeframe_s=3600)
        await candle_persister.warmup(
            buffer=comp.streaming.candle_buffer,
            exchange=comp.exchange,
            min_bars=500,
        )

        from .infrastructure.monitoring.stream_integrity import (
            StreamIntegrityMonitor,
        )
        from .infrastructure.monitoring.system_health import (
            SystemHealthCollector,
        )
        stream_key = f"ticks:{symbol.replace('/', '').lower()}"

        stream_monitor = StreamIntegrityMonitor(stream_key=stream_key)

        health_collector = SystemHealthCollector(
            get_buffer_snapshot=comp.streaming.candle_buffer.snapshot,
            get_inference_pipeline=comp.streaming.inference_pipeline,
            get_signal_processor=comp.streaming.signal_processor,
            get_stream_integrity=stream_monitor,
            get_candle_builder=comp.streaming.candle_builder,
        )

        if comp.mode != "BACKTESTING":
            from .infrastructure.trading.execution_guard import (
                ExecutionGuard,
            )
            from .infrastructure.monitoring.phase_b_tracker import (
                PhaseBTracker,
            )
            from .infrastructure.monitoring.experiment_control_plane import (
                BaselineDominanceChecker,
                ExperimentSnapshot,
                HealthCheckAggregator,
                RollingSharpeGuard,
            )
            from .infrastructure.edl.trade_episode import (
                TradeEpisodeStore,
            )

            execute_uc = _build_execute_uc(comp)
            monitor_uc = _build_monitor_uc(comp)
            guard = ExecutionGuard(execute_fn=execute_uc.execute)

            initial_capital = Decimal(os.environ.get("PAPER_CAPITAL", "1000"))
            phase_b_tracker = PhaseBTracker(
                candle_buffer=comp.streaming.candle_buffer,
                position_repo=comp.position_repo,
                symbol=symbol,
                initial_capital=initial_capital,
            )

            # ECL: initialize experiment components
            ecl_snapshot = ExperimentSnapshot(
                symbol=symbol,
                timeframe="1h",
                execution_mode=comp.mode,
                initial_capital=initial_capital,
                model_version="",
                git_commit=os.environ.get("GIT_HASH", ""),
            )
            ecl_snapshot.emit()
            ecl_sharpe_guard = RollingSharpeGuard()
            ecl_baseline_checker = BaselineDominanceChecker()
            ecl_health_aggregator = HealthCheckAggregator(
                sharpe_guard=ecl_sharpe_guard,
                baseline_checker=ecl_baseline_checker,
                initial_capital=initial_capital,
            )

            episode_store = TradeEpisodeStore()

            t1 = asyncio.create_task(
                _tick_to_candle_loop(comp, redis_client, stream_monitor, candle_persister)
            )
            t2 = asyncio.create_task(
                _decision_loop(comp, guard, health_collector, phase_b_tracker, redis_client, symbol, episode_store)
            )
            t3 = asyncio.create_task(
                _position_monitor_loop(comp, monitor_uc)
            )
            t5 = asyncio.create_task(
                _health_snapshot_loop(health_collector)
            )
            t6 = asyncio.create_task(
                _stream_idle_check_loop(stream_monitor)
            )
            t7 = asyncio.create_task(
                _phase_b_loop(phase_b_tracker, ecl_health_aggregator, episode_store)
            )
            t8 = asyncio.create_task(
                _shadow_outcome_loop(redis_client, comp, symbol)
            )
            t9 = asyncio.create_task(
                _shadow_outcome_loop(redis_client, comp, symbol)
            )
            streaming_tasks = [t1, t2, t3, t5, t6, t7, t9]

            t4 = asyncio.create_task(
                _system_diagnostics_loop(comp, health_collector, redis_client, streaming_tasks)
            )
            streaming_tasks.append(t4)

            # Fix 4: Live Drift Watchdog
            if comp.drift_watchdog is not None:
                comp.drift_watchdog.start()
                log.info("drift_watchdog_started")

            # Fix 5: Idempotency cleanup loop
            if comp.idempotency_store is not None:
                t8 = asyncio.create_task(
                    _idempotency_cleanup_loop(comp.idempotency_store)
                )
                maintenance_tasks.append(t8)

            log.info("streaming_loops_started", count=len(streaming_tasks))
        else:
            log.info("streaming_components_available_skipping_loops_mode", mode=comp.mode)
    else:
        log.warning("streaming_components_not_available")

    try:
        yield
    finally:
        if comp.drift_watchdog is not None:
            await comp.drift_watchdog.stop()
        for t in streaming_tasks:
            t.cancel()
        if streaming_tasks:
            await asyncio.gather(*streaming_tasks, return_exceptions=True)
        for t in maintenance_tasks:
            t.cancel()
        if maintenance_tasks:
            await asyncio.gather(*maintenance_tasks, return_exceptions=True)
        if redis_client is not None:
            try:
                await redis_client.aclose()
            except Exception as e_redis:
                log.warning("redis_close_error", error=str(e_redis))
        if comp.exchange is not None:
            try:
                await comp.exchange.close()
            except Exception as e:
                log.warning("ccxt_close_error", error=str(e))


app.router.lifespan_context = lifespan


# ── Streaming loops ──────────────────────────────────────────────


async def _tick_to_candle_loop(
    comp: Composition,
    client: redis.Redis,
    stream_monitor: object,
    candle_persister: Any | None = None,
) -> None:
    """Loop 1: consume ticks from Redis Streams, build 1h candles, persist.

    Single ingestion path: Redis XREAD-only. gRPC has been removed.
    MAXLEN ~2000 on the Redis stream keeps memory bounded.
    """
    from .infrastructure.monitoring.stream_integrity import (
        StreamIntegrityMonitor,
    )
    from .domain.recovery.stream_barrier import StreamConsumptionBarrier
    from .infrastructure.trading.candle_builder import CandleBuilder

    import json

    symbol = os.environ.get("SYMBOL", "BTC/USDT")
    stream_key = f"ticks:{symbol.replace('/', '').lower()}"
    last_id = "$"
    LAG_WARN_MS = 5000

    streaming = comp.streaming
    assert streaming is not None
    builder: CandleBuilder = streaming.candle_builder
    buffer = streaming.candle_buffer
    integrity: StreamIntegrityMonitor = stream_monitor
    barrier: StreamConsumptionBarrier | None = comp.stream_barrier

    dropped = 0
    dedup_cache: set[str] = set()
    dedup_ring: deque[str] = deque()
    dedup_skipped = 0
    DEDUP_MAX = 20000
    log.info("tick_candle_loop_started", stream=stream_key)

    try:
        while True:
            try:
                res = await client.xread({stream_key: last_id}, block=1000, count=100)
            except Exception as e:
                log.warning("xread_error", error=str(e))
                await asyncio.sleep(1)
                continue

            if not res:
                integrity.check_idle()
                continue

            for item in res:
                stream_name = getattr(item, "name", item[0] if isinstance(item, (list, tuple)) else b"")
                entries = getattr(item, "entries", item[1] if isinstance(item, (list, tuple)) else [])
                for entry_id, fields in entries:
                    last_id = (
                        entry_id.decode()
                        if isinstance(entry_id, (bytes, bytearray))
                        else entry_id
                    )
                    if isinstance(fields, dict):
                        field_items = fields.items()
                    elif isinstance(fields, list):
                        field_items = fields
                    else:
                        field_items = []
                    decoded = {
                        (k.decode() if isinstance(k, (bytes, bytearray)) else k): (
                            v.decode() if isinstance(v, (bytes, bytearray)) else v
                        )
                        for k, v in field_items
                    }
                    payload = decoded.get("p")
                    if not payload:
                        continue
                    try:
                        tick = json.loads(payload)
                    except Exception as e:
                        log.warning("tick_parse_error", error=str(e))
                        continue

                    price_dec = tick.get("price", {})
                    price = float(price_dec.get("minor", 0)) / (
                        10 ** int(price_dec.get("decimals", 8))
                    )
                    volume = float(tick.get("quantity", "0")) / 1e8
                    ts_ms = int(tick.get("ts", 0))
                    trade_id = str(tick.get("tradeId", ""))

                    if ts_ms == 0 or price == 0.0:
                        continue

                    now_ms = time.time() * 1000
                    lag_ms = now_ms - ts_ms
                    if lag_ms > LAG_WARN_MS:
                        log.warning("stream_backpressure_detected", lag_ms=int(round(lag_ms)))

                    if barrier is not None and not barrier.should_process(ts_ms):
                        dropped += 1
                        if dropped % 1000 == 0:
                            log.info(
                                "barrier_dropped_ticks",
                                dropped=dropped,
                                barrier_open=barrier.is_open,
                            )
                        continue

                    # Dedup: skip ticks with tradeId already seen in last DEDUP_MAX ticks
                    if trade_id and trade_id in dedup_cache:
                        dedup_skipped += 1
                        if dedup_skipped % 100 == 1:
                            log.debug("tick_dedup_skipped", trade_id=trade_id, total=dedup_skipped)
                        continue
                    if trade_id:
                        dedup_cache.add(trade_id)
                        dedup_ring.append(trade_id)
                        if len(dedup_ring) > DEDUP_MAX:
                            oldest = dedup_ring.popleft()
                            dedup_cache.discard(oldest)

                    integrity.record_tick(trade_id, ts_ms)

                    update = builder.update(ts_ms=ts_ms, price=price, volume=volume)
                    if update.completed is not None:
                        _pipeline_latency.tick_to_candle_ms.append(
                            time.time() * 1000 - ts_ms
                        )
                        buffer.append(update.completed)
                        if candle_persister is not None:
                            asyncio.create_task(candle_persister.save(update.completed))

    except asyncio.CancelledError:
        log.info("tick_candle_loop_cancelled")
        raise


async def _decision_loop(
    comp: Composition,
    guard: object,
    health_collector: object,
    phase_b_tracker: object,
    redis_client: Any,
    symbol: str,
    episode_store: object | None = None,
) -> None:
    """Loop 2: every ~1s, check for new 1h candle, run inference.

    Candles are built directly at 1h by CandleBuilder. No aggregation
    needed. Runs inference when 500+ candles are available (≈21 days
    of data — enough for MTF 4h/1d features).
    """
    from .infrastructure.trading.execution_guard import ExecutionGuard
    from .infrastructure.monitoring.system_health import (
        SystemHealthCollector,
    )
    from .infrastructure.edl.trade_episode import (
        FeatureCanonicalizer,
        TradeEpisode,
        TradeEpisodeStore,
    )

    streaming = comp.streaming
    assert streaming is not None

    pipeline = streaming.inference_pipeline
    buffer = streaming.candle_buffer
    guard_: ExecutionGuard = guard
    health: SystemHealthCollector = health_collector

    last_inference_ms: int = 0
    TF_1H_MS = 3_600_000
    MIN_WARMUP_BARS = 500

    log.info("decision_loop_started")

    try:
        while True:
            try:
                await asyncio.sleep(1.0)

                candles = buffer.to_ohlcv_dicts()
                if len(candles) < 5:
                    continue

                last_ts = int(candles[-1].get("timestamp", 0))
                if last_ts <= last_inference_ms:
                    continue
                if last_ts - last_inference_ms < TF_1H_MS:
                    continue
                if len(candles) < MIN_WARMUP_BARS:
                    continue

                _decision_ts = time.monotonic_ns()

                if not pipeline.is_loaded:
                    ok = await pipeline.load_checkpoint()
                    if not ok:
                        log.warning("checkpoint_not_loaded_skipping")
                        continue

                last_inference_ms = (
                    int(candles[-1].get("timestamp", 0)) // TF_1H_MS
                ) * TF_1H_MS

                log.info("inference_triggered", candles_1h=len(candles), ts=last_inference_ms)

                t0 = time.monotonic()
                result = await pipeline.predict(candles)
                latency_ms = (time.monotonic() - t0) * 1000

                _pipeline_latency.candle_to_decision_ms.append(
                    (time.monotonic_ns() - _decision_ts) / 1_000_000
                )

                has_signal = result.signal is not None
                health.record_inference(latency_ms, has_signal)

                if has_signal and redis_client is not None:
                    last = candles[-1] if candles else {}
                    from .infrastructure.shadow.shadow_producer import (
                        produce_shadow_decision,
                    )
                    asyncio.create_task(
                        produce_shadow_decision(
                            redis_client,
                            symbol,
                            result.signal.side.value,
                            result.ensemble_confidence,
                            result.candle_close,
                            last,
                            int(candles[-1].get("timestamp", 0)),
                            result.model_version,
                            result.regime,
                        )
                    )

                if not has_signal:
                    log.info(
                        "inference_no_signal",
                        confidence=result.ensemble_confidence,
                        reason=result.error or "below_threshold",
                    )
                    continue

                log.info(
                    "inference_signal",
                    side=result.signal.side.value,
                    confidence=result.ensemble_confidence,
                    model=result.model_version,
                )

                # H63: fire-and-forget shadow decision recording
                if redis_client is not None:
                    from .infrastructure.shadow.shadow_producer import (
                        produce_shadow_decision,
                    )
                    asyncio.create_task(
                        produce_shadow_decision(
                            redis_client,
                            symbol,
                            result.signal.side.value,
                            result.ensemble_confidence,
                            result.candle_close,
                            candles[-1] if candles else {},
                            int(candles[-1].get("timestamp", 0)) if candles else 0,
                            result.model_version,
                            result.regime,
                        )
                    )

                proc_result = streaming.signal_processor.process(
                    result.signal,
                    price=Decimal(str(result.candle_close)),
                )

                phase_b_tracker.record_signal(
                    side=result.signal.side.value,
                    confidence=result.ensemble_confidence,
                    regime=result.regime,
                    model_version=result.model_version,
                    accepted=proc_result.accepted,
                    reason=proc_result.reason if not proc_result.accepted else "",
                )

                if not proc_result.accepted or proc_result.execution_signal is None:
                    log.info(
                        "signal_rejected",
                        reason=proc_result.reason,
                    )
                    continue

                log.info(
                    "signal_accepted",
                    side=proc_result.execution_signal.side.value,
                    confidence=proc_result.execution_signal.confidence,
                )

                proc_result.execution_signal.metadata["source"] = "STREAMING"
                exec_result = await guard_.execute(proc_result.execution_signal)
                exec_approved = hasattr(exec_result, "report")

                if exec_approved:
                    streaming.signal_processor.confirm_signal(
                        proc_result.execution_signal.side,
                    )

                if exec_approved and hasattr(exec_result.report, "position_id"):
                    position_id = exec_result.report.position_id
                    entry_price = exec_result.report.avg_price
                else:
                    position_id = None
                    entry_price = None

                episode_id = ""
                feature_hash = ""
                if exec_approved and position_id is not None and episode_store is not None:
                    store: TradeEpisodeStore = episode_store
                    feat = pipeline.last_raw_features
                    if feat is not None:
                        feature_hash = FeatureCanonicalizer.canonicalize(feat)
                    episode = TradeEpisode(
                        side=proc_result.execution_signal.side.value,
                        model_version=result.model_version,
                        regime_at_entry=result.regime,
                        feature_hash=feature_hash,
                    )
                    store.append(episode)
                    episode_id = episode.episode_id
                    log.info("[edl] episode_created", episode_id=episode_id, position_id=position_id)

                phase_b_tracker.record_execution(
                    signal_id=proc_result.execution_signal.signal_id,
                    position_id=position_id,
                    side=proc_result.execution_signal.side.value,
                    confidence=proc_result.execution_signal.confidence,
                    regime=result.regime,
                    model_version=result.model_version,
                    entry_price=entry_price,
                    episode_id=episode_id,
                    feature_hash=feature_hash,
                )

                if exec_approved:
                    health.record_execution()
                    log.info("signal_executed", signal_id=proc_result.execution_signal.signal_id)
                else:
                    reason = getattr(exec_result, "reason", "unknown")
                    log.info("signal_not_executed", reason=reason)
            except Exception as exc:
                log.error("decision_loop_error", error=str(exc))

    except asyncio.CancelledError:
        log.info("decision_loop_cancelled")
        raise


async def _shadow_outcome_loop(
    redis_client: Any,
    comp: Composition,
    symbol: str,
) -> None:
    """Loop 4: periodically evaluate shadow decisions against market outcome."""
    from .infrastructure.shadow.shadow_outcome_worker import (
        shadow_outcome_worker_loop,
    )

    assert comp.streaming is not None
    await shadow_outcome_worker_loop(
        client=redis_client,
        candle_buffer=comp.streaming.candle_buffer,
        symbol=symbol,
    )


async def _shadow_outcome_loop(
    redis_client: Any,
    comp: Composition,
    symbol: str,
) -> None:
    """Loop 4: periodically evaluate shadow decisions against market outcome."""
    from .infrastructure.shadow.shadow_outcome_worker import (
        shadow_outcome_worker_loop,
    )

    assert comp.streaming is not None
    await shadow_outcome_worker_loop(
        client=redis_client,
        candle_buffer=comp.streaming.candle_buffer,
        symbol=symbol,
    )


async def _position_monitor_loop(
    comp: Composition,
    monitor_uc: MonitorPositionsUseCase,
) -> None:
    """Loop 3: every 5s, check open positions and drawdown."""
    log.info("position_monitor_loop_started")

    try:
        while True:
            await asyncio.sleep(5.0)

            if comp.mode == "BACKTESTING":
                continue

            is_halted = await comp.check_drawdown.is_halted()
            if is_halted:
                log.warning("drawdown_halted_skipping_position_check")
                continue

            try:
                result = await monitor_uc.run()
                if result.closed > 0 or result.sl_updates > 0 or result.be_activations > 0:
                    log.info(
                        "position_monitor_result",
                        closed=result.closed,
                        held=result.held,
                        sl_updates=result.sl_updates,
                        be_activations=result.be_activations,
                        trail_activations=result.trail_activations,
                    )
            except Exception as exc:
                log.error("position_monitor_error", error=str(exc))

    except asyncio.CancelledError:
        log.info("position_monitor_loop_cancelled")
        raise


async def _system_diagnostics_loop(
    comp: Composition,
    health_collector: object,
    redis_client: redis.Redis,
    all_tasks: list[asyncio.Task],
) -> None:
    """Loop 4: every 60s, validate all loops and components are healthy.

    NEVER raises. Logs ``system_degraded_state`` if anything is wrong.
    """
    from .infrastructure.monitoring.system_health import (
        SystemHealthCollector,
    )

    log.info("diagnostics_loop_started")
    health: SystemHealthCollector = health_collector

    try:
        while True:
            await asyncio.sleep(60.0)

            degraded: list[str] = []

            alive = [t for t in all_tasks if not t.done()]
            if len(alive) < len(all_tasks):
                dead = [t for t in all_tasks if t.done()]
                for t in dead:
                    exc = t.exception()
                    degraded.append(f"task_dead: {t.get_name()}, error={exc}")

            if comp.streaming is not None:
                pipe = comp.streaming.inference_pipeline
                if not pipe.is_loaded:
                    degraded.append("inference_pipeline_not_loaded")
                load_err = getattr(pipe, "load_error", "")
                if load_err:
                    degraded.append(f"inference_pipeline_error: {load_err}")

                buf = comp.streaming.candle_buffer
                if len(buf) == 0:
                    degraded.append("candle_buffer_empty")

            try:
                pong = await redis_client.ping()
                if not pong:
                    degraded.append("redis_ping_failed")
            except Exception as e:
                degraded.append(f"redis_unreachable: {e}")

            if degraded:
                log.warning(
                    "system_degraded_state",
                    issues=degraded,
                    alive_loops=len(alive),
                    total_loops=len(all_tasks),
                )
            else:
                log.info(
                    "system_diagnostics_ok",
                    alive_loops=len(alive),
                )

    except asyncio.CancelledError:
        log.info("diagnostics_loop_cancelled")
        raise


async def _health_snapshot_loop(health_collector: object) -> None:
    """Loop 5: emit a system health snapshot every 10 minutes."""
    from .infrastructure.monitoring.system_health import (
        SystemHealthCollector,
    )

    health: SystemHealthCollector = health_collector

    try:
        while True:
            await asyncio.sleep(600.0)
            health.emit()
    except asyncio.CancelledError:
        pass


async def _stream_idle_check_loop(stream_monitor: object) -> None:
    """Loop 6: periodic idle check (every 10s) for the stream monitor."""
    from .infrastructure.monitoring.stream_integrity import (
        StreamIntegrityMonitor,
    )

    monitor: StreamIntegrityMonitor = stream_monitor

    try:
        while True:
            await asyncio.sleep(10.0)
            monitor.check_idle()
    except asyncio.CancelledError:
        pass


async def _phase_b_loop(
    phase_b_tracker: object,
    ecl_health: object | None = None,
    episode_store: object | None = None,
) -> None:
    """Loop 7: Phase B metric collection every 60 seconds.

    Drives the PhaseBTracker: polls closed positions, updates market
    baselines, checks kill-switch thresholds, emits periodic metrics
    and daily reports.

    When ``ecl_health`` is provided, emits ``phase_b_experiment_health``
    after each tick.
    When ``episode_store`` is provided, settles TradeEpisodes for
    newly closed positions.
    """
    from .infrastructure.monitoring.experiment_control_plane import (
        HealthCheckAggregator,
    )
    from .infrastructure.monitoring.phase_b_tracker import (
        PhaseBTracker,
        PhaseBTradeEntry,
    )
    from .infrastructure.edl.trade_episode import (
        TradeEpisodeStore,
    )

    tracker: PhaseBTracker = phase_b_tracker
    aggregator: HealthCheckAggregator | None = ecl_health
    store: TradeEpisodeStore | None = episode_store

    # Track which episodes we already settled
    _settled_up_to: int = 0

    try:
        while True:
            await asyncio.sleep(60.0)
            await tracker.tick()
            if aggregator is not None:
                aggregator.emit(
                    system_pnl=tracker.total_pnl,
                    bnh_pnl=tracker.bnh_pnl,
                    ema_pnl=tracker.ema_cross_pnl,
                    daily_returns=tracker.daily_returns,
                    drawdown_pct=tracker.max_drawdown_pct,
                )

            # EDL: settle newly closed trades
            if store is not None:
                closed = tracker.all_closed_since(_settled_up_to)
                for entry in closed:
                    if not entry.episode_id:
                        continue
                    ep = store.get(entry.episode_id)
                    if ep is not None and ep.t_exit is None:
                        settled = ep.settle(
                            t_exit=entry.closed_at,
                            pnl=entry.realized_pnl,
                        )
                        store.append_settled(settled)
                        log.info(
                            "[edl] episode_settled",
                            episode_id=entry.episode_id,
                            pnl=str(entry.realized_pnl),
                        )
                _settled_up_to = tracker.trade_count
    except asyncio.CancelledError:
        pass


# ── Streaming use case builders ──────────────────────────────────


def _build_execute_uc(comp: Composition) -> object:
    """Build ExecuteSignalUseCase for the streaming loop."""
    from decimal import Decimal

    from .application.use_cases.execute_signal import ExecuteSignalUseCase
    from .domain.entities.signal_validator import SignalValidator
    from .infrastructure.balance.ccxt_balance_provider import (
        CcxtBalanceProvider,
    )
    from .infrastructure.execution.in_memory_position_repo import (
        InMemoryPositionRepository,
    )
    from .infrastructure.execution.structlog_execution_logger import (
        StructlogExecutionLogger,
    )
    from .infrastructure.indicators.ta_atr_calculator import TaAtrCalculator
    from .infrastructure.ohlcv.ccxt_ohlcv_source import ccxt_ohlcv_source

    from .composition import _build_order_client

    if comp.exchange is None:
        raise RuntimeError("exchange is None")

    balance_provider = CcxtBalanceProvider(exchange=comp.exchange)
    atr_calc = TaAtrCalculator(
        source=lambda s, t, w: ccxt_ohlcv_source(comp.exchange, s, t, w)
    )

    async def _is_halted() -> bool:
        return await comp.check_drawdown.is_halted()

    from .infrastructure.execution.in_memory_position_repo import InMemoryPositionRepository
    position_repo = comp.position_repo if comp.position_repo else InMemoryPositionRepository()

    # Fix 2+3: Inject idempotency store + snapshot repo for execution dedup and flush
    idempotency_store = getattr(comp, "idempotency_store", None)
    snapshot_repo = getattr(comp, "system_snapshot_repo", None)

    return ExecuteSignalUseCase(
        signal_validator=SignalValidator(),
        balance_provider=balance_provider,
        atr_calculator=atr_calc,
        exchange_client=_build_order_client(comp.exchange),
        position_repo=position_repo,
        execution_logger=StructlogExecutionLogger(),
        is_halted=_is_halted,
        idempotency_store=idempotency_store,
        snapshot_repo=snapshot_repo,
    )


def _build_monitor_uc(comp: Composition) -> object:
    """Build MonitorPositionsUseCase for the streaming loop."""
    from decimal import Decimal

    from .application.use_cases.monitor_positions import MonitorPositionsUseCase
    from .infrastructure.execution.in_memory_position_repo import InMemoryPositionRepository
    from .infrastructure.execution.structlog_execution_logger import (
        StructlogExecutionLogger,
    )
    from .composition import _build_order_client

    if comp.exchange is None:
        raise RuntimeError("exchange is None")

    exchange_client = _build_order_client(comp.exchange)
    if hasattr(exchange_client, "get_price"):
        price_provider = exchange_client.get_price
    else:
        async def _ccxt_price(symbol: str) -> Decimal:
            try:
                ticker = await comp.exchange.fetch_ticker(symbol)
            except Exception as e:
                log.error("price_fetch_failed", symbol=symbol, error=str(e))
                return Decimal("0")
            last = ticker.get("last") or ticker.get("close") or 0.0
            return Decimal(str(last))
        price_provider = _ccxt_price

    position_repo = comp.position_repo if comp.position_repo else InMemoryPositionRepository()
    return MonitorPositionsUseCase(
        position_repo=position_repo,
        exchange_client=exchange_client,
        execution_logger=StructlogExecutionLogger(),
        price_provider=price_provider,
    )


async def _idempotency_cleanup_loop(
    idempotency_store: object,
) -> None:
    """Loop 8: cleanup idempotency entries older than 24h every hour.

    Fix 5: Runs PRAGMA optimize after each cleanup.
    """
    import structlog
    log = structlog.get_logger()
    try:
        while True:
            await asyncio.sleep(3600.0)
            try:
                deleted = await idempotency_store.cleanup_old_entries(
                    retention_hours=24
                )
                if deleted > 0:
                    log.info("idempotency_cleanup", deleted=deleted)
            except Exception as e:
                log.warning("idempotency_cleanup_error", error=str(e))
    except asyncio.CancelledError:
        pass


# ── Health ───────────────────────────────────────────────────────

_boot_timestamp: float = 0.0
_WARMUP_SECONDS = 30

# ── Pipeline latency tracking (S5-T3) ─────────────────────────────────

_PIPELINE_WINDOW = 1000


@dataclass
class _PipelineLatencyTracker:
    tick_to_candle_ms: deque[float] = field(default_factory=lambda: deque(maxlen=_PIPELINE_WINDOW))
    candle_to_decision_ms: deque[float] = field(default_factory=lambda: deque(maxlen=_PIPELINE_WINDOW))
    tick_arrival_ts_ns: int = 0

    def _percentile(self, samples: deque[float], p: float) -> float:
        if not samples:
            return 0.0
        arr = sorted(samples)
        idx = max(0, min(len(arr) - 1, int(len(arr) * p / 100)))
        return arr[idx]

    def p50_tick_to_candle(self) -> float:
        return self._percentile(self.tick_to_candle_ms, 50)

    def p99_tick_to_candle(self) -> float:
        return self._percentile(self.tick_to_candle_ms, 99)

    def p50_candle_to_decision(self) -> float:
        return self._percentile(self.candle_to_decision_ms, 50)

    def p99_candle_to_decision(self) -> float:
        return self._percentile(self.candle_to_decision_ms, 99)

    def snapshot(self) -> dict:
        return {
            "tick_to_candle_ms_p50": round(self.p50_tick_to_candle(), 2),
            "tick_to_candle_ms_p99": round(self.p99_tick_to_candle(), 2),
            "candle_to_decision_ms_p50": round(self.p50_candle_to_decision(), 2),
            "candle_to_decision_ms_p99": round(self.p99_candle_to_decision(), 2),
            "samples_tick_to_candle": len(self.tick_to_candle_ms),
            "samples_candle_to_decision": len(self.candle_to_decision_ms),
        }


_pipeline_latency = _PipelineLatencyTracker()


@app.get("/health")
async def health() -> dict:
    mode = os.environ.get("ENVIRONMENT_MODE", "PAPER_TRADING")
    uptime = time.monotonic() - _boot_timestamp if _boot_timestamp > 0 else 0
    status = "starting_up" if uptime < _WARMUP_SECONDS else "ok"
    return {
        "status": status,
        "mode": mode,
        "uptime_s": round(uptime, 1),
    }


@app.get("/health/pipeline")
async def health_pipeline() -> dict:
    return _pipeline_latency.snapshot()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)
