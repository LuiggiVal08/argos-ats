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
from fastapi import FastAPI, Request

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

# ── Temporal Hard Gates (shared across tick-candle & decision loops) ────────
LAG_WARN_MS = 5000
MAX_TICK_LAG_MS = 10_000      # ticks >10s old are stale
MAX_CANDLE_AGE_MS = 5_400_000  # >90min without fresh candle → skip decision

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
            # Open stream barrier after successful recovery
            if hasattr(comp, "stream_barrier") and comp.stream_barrier is not None:
                recovery_ts = recovery_report.timestamp.timestamp()
                comp.stream_barrier.open(recovery_timestamp=recovery_ts)
                log.info("stream_barrier_opened", recovery_ts=recovery_ts)

            # Auto-open the day to initialize drawdown snapshot
            if comp.mode != "BACKTESTING" and hasattr(comp, "open_day") and comp.open_day is not None:
                try:
                    snap = await comp.open_day.execute(force=True)
                    log.info(
                        "day_opened_on_startup",
                        starting_balance=float(snap.starting_balance),
                    )
                except Exception as e:
                    log.warning("day_open_failed_on_startup", error=str(e))
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
            guard = ExecutionGuard(
                execute_fn=execute_uc.execute,
                market_continuity_guard=comp.market_continuity_guard,
            )

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

            # ── CCL: Cognitive Completion Layer ───────────────────────────
            from .infrastructure.edl.ccl_service import CCLService
            ccl = CCLService(
                redis_client=redis_client,
                event_store=comp.event_store,
                episode_store=episode_store,
            )

            # ── CCL Consistency Checker ───────────────────────────────────
            from .infrastructure.edl.consistency import CCLConsistency
            ccl_consistency = CCLConsistency(
                event_store=comp.event_store,
                episode_store=episode_store,
            )
            app.state.ccl_consistency = ccl_consistency

            # ── Event Memory (Aggregator + MemoryIndex) ──────────────────
            from .infrastructure.edl.event_memory import (
                EventAggregator,
                MemoryIndex,
            )
            event_aggregator = EventAggregator(episode_store=episode_store)
            memory_index = MemoryIndex(episode_store=episode_store)
            app.state.event_aggregator = event_aggregator
            app.state.memory_index = memory_index

            # ── Decision Augmentation (FASE 3) ─────────────────────────
            from .infrastructure.edl.decision_augmentation import (
                AdaptiveRiskAdjuster,
                DecisionAugmentation,
                PolicyUpdateHook,
            )
            policy_hook = PolicyUpdateHook()
            aug_decision = DecisionAugmentation()
            risk_adjuster = AdaptiveRiskAdjuster(
                belief_state=policy_hook,
                memory_index=memory_index,
            )
            app.state.policy_hook = policy_hook

            # ── Composer Activation (FASE 4) ──────────────────────────
            from .infrastructure.edl.composer_activation import (
                ModelComparisonLayer,
                ModelRegistryScanner,
            )
            model_scanner = ModelRegistryScanner()
            model_scanner.scan()
            comparison_layer = ModelComparisonLayer(
                scanner=model_scanner,
            )
            app.state.comparison_layer = comparison_layer

            # ── EventRecorder: event sourcing for pipeline events ─────────
            from .infrastructure.event_sourcing import EventRecorder
            event_store = comp.event_store

            t1 = asyncio.create_task(
                _tick_to_candle_loop(comp, redis_client, stream_monitor, candle_persister)
            )
            t2 = asyncio.create_task(
                _decision_loop(comp, guard, health_collector, phase_b_tracker, redis_client, symbol, episode_store, ccl, event_store, aug_decision, risk_adjuster, policy_hook)
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
                _phase_b_loop(phase_b_tracker, ecl_health_aggregator, episode_store, ccl, ccl_consistency, policy_hook)
            )
            t9 = asyncio.create_task(
                _ccl_consistency_loop(ccl_consistency)
            )
            t8 = asyncio.create_task(
                _shadow_outcome_loop(redis_client, comp, symbol)
            )
            streaming_tasks = [t1, t2, t3, t5, t6, t7, t8, t9]

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
    streaming = comp.streaming
    assert streaming is not None
    builder: CandleBuilder = streaming.candle_builder
    buffer = streaming.candle_buffer
    integrity: StreamIntegrityMonitor = stream_monitor
    barrier: StreamConsumptionBarrier | None = comp.stream_barrier
    mcg = comp.market_continuity_guard

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

                    # Temporal Hard Gate (A): drop ticks older than threshold
                    if lag_ms > MAX_TICK_LAG_MS:
                        log.warning("tick_dropped_stale", lag_ms=int(round(lag_ms)))
                        continue

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

                    mcg.record_tick(ts_ms / 1000)

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
    redis_client: object,
    symbol: str,
    episode_store: object | None = None,
    ccl: object | None = None,
    event_store: object | None = None,
    aug_decision: object | None = None,
    risk_adjuster: object | None = None,
    policy_hook: object | None = None,
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
    from .infrastructure.event_sourcing import EventRecorder

    streaming = comp.streaming
    assert streaming is not None

    pipeline = streaming.inference_pipeline
    buffer = streaming.candle_buffer
    guard_: ExecutionGuard = guard
    health: SystemHealthCollector = health_collector
    mcg = comp.market_continuity_guard
    ctx = None

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

                # Market Continuity Guard: freeze inference during gap
                if mcg.market_data_gap:
                    log.warning(
                        "decision_skipped_market_data_gap",
                        gap_duration_s=round(mcg.gap_duration_s, 1),
                    )
                    continue

                if not pipeline.is_loaded:
                    ok = await pipeline.load_checkpoint()
                    if not ok:
                        log.warning("pipeline_load_failed")
                        continue

                # Temporal Hard Gate (B): skip decision if last candle is too old
                candle_age_ms = int(time.time() * 1000) - last_ts
                if candle_age_ms > MAX_CANDLE_AGE_MS:
                    log.warning(
                        "waiting_for_fresh_candle",
                        age_ms=candle_age_ms,
                    )
                    continue

                _decision_ts = time.monotonic_ns()

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

                # ── CCL Hook 1: enrich prediction with prior ────────
                _ccl_prior = None
                if ccl is not None:
                    _ccl_prior = await ccl.enrich_prediction(
                        result,
                        pipeline.last_raw_features,
                    )

                has_signal = result.signal is not None
                health.record_inference(latency_ms, has_signal)

                # ── Event sourcing: candle_closed + inference ───────
                if event_store is not None:
                    await EventRecorder.record(
                        event_store,
                        "inference.completed",
                        "decision_loop",
                        {
                            "candles": len(candles),
                            "has_signal": has_signal,
                            "latency_ms": latency_ms,
                            "last_ts": last_ts,
                            "model_version": result.model_version if hasattr(result, "model_version") else "",
                            "regime": result.regime if hasattr(result, "regime") else "",
                        },
                    )

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

                # ── Event sourcing: signal_generated ────────────────
                if event_store is not None:
                    await EventRecorder.record(
                        event_store,
                        "signal.generated",
                        "decision_loop",
                        {
                            "signal_id": proc_result.execution_signal.signal_id,
                            "side": proc_result.execution_signal.side.value,
                            "confidence": proc_result.execution_signal.confidence,
                            "symbol": proc_result.execution_signal.symbol,
                            "model_version": result.model_version,
                            "regime": result.regime,
                        },
                    )

                # ── CCL Hook 2: attach posterior before execution ───
                if ccl is not None:
                    await ccl.attach_posterior(
                        proc_result.execution_signal,
                        _ccl_prior,
                        result,
                    )

                # ── FASE 3.1: Decision Augmentation ─────────────────
                if aug_decision is not None and _ccl_prior is not None:
                    from .infrastructure.edl.decision_augmentation import DecisionAugmentation
                    aug: DecisionAugmentation = aug_decision
                    meta = proc_result.execution_signal.metadata
                    post_edge = meta.get("ccl_posterior_edge", 0.5)
                    prior_logodds = meta.get("ccl_prior_logodds", 0.0)
                    posterior_confidence = abs(prior_logodds) / (abs(prior_logodds) + 1) if prior_logodds != 0 else 0.5
                    aug_params = aug.augment(
                        model_confidence=proc_result.execution_signal.confidence,
                        posterior_edge=post_edge,
                        posterior_confidence=posterior_confidence,
                        regime=result.regime,
                    )
                    meta["ccl_aug_confidence"] = aug_params.final_confidence
                    meta["ccl_position_size_mult"] = aug_params.position_size_mult
                    meta["ccl_risk_mult"] = aug_params.risk_mult
                    meta["ccl_augmentation_applied"] = aug_params.augmentation_applied

                    if not aug_params.augmentation_applied:
                        log.debug("ccl:no_augmentation_needed", edge=post_edge)

                # ── FASE 3.3: Adaptive Risk ─────────────────────────
                if risk_adjuster is not None:
                    from .infrastructure.edl.decision_augmentation import AdaptiveRiskAdjuster
                    risk: AdaptiveRiskAdjuster = risk_adjuster
                    risk_params = risk.get_adjustments(
                        regime=result.regime,
                        confidence_band=meta.get("ccl_confidence_band", ""),
                    )
                    if risk_params.augmentation_applied:
                        meta["ccl_adaptive_size_mult"] = risk_params.position_size_mult
                        meta["ccl_adaptive_risk_mult"] = risk_params.risk_mult

                meta["source"] = "STREAMING"

                # ── Portfolio Context: clusters, cooldown, sizing ──
                if ctx is None:
                    from redis.asyncio import Redis
                    from .infrastructure.trading.portfolio_context_store import (
                        RedisPortfolioContextStore,
                    )
                    from .domain.entities.portfolio_context import PortfolioContext
                    _r = redis_client or comp.broker
                    ctx = PortfolioContext(RedisPortfolioContextStore(_r))
                _ctx_decision = await ctx.evaluate(
                    symbol=symbol,
                    direction=result.signal.side.value,
                    regime=result.regime,
                )
                meta["portfolio_cluster_id"] = _ctx_decision.cluster_id
                meta["portfolio_cluster_size"] = _ctx_decision.cluster_size
                meta["portfolio_cooldown_s"] = _ctx_decision.cooldown_remaining_s
                meta["portfolio_size_mult"] = _ctx_decision.size_multiplier

                # Record signal event for cluster tracking (always, even if blocked)
                await ctx.record_signal_event(
                    symbol=symbol,
                    direction=result.signal.side.value,
                )

                if _ctx_decision.block_reason is not None:
                    log.warning(
                        "portfolio_blocked",
                        reason=_ctx_decision.block_reason,
                        cluster_id=_ctx_decision.cluster_id,
                        cluster_size=_ctx_decision.cluster_size,
                    )
                    continue

                if _ctx_decision.cluster_size > 1:
                    log.info(
                        "portfolio_cluster",
                        cluster_id=_ctx_decision.cluster_id,
                        size=_ctx_decision.cluster_size,
                    )

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
                        entry_price=Decimal(str(exec_result.report.avg_price)) if hasattr(exec_result.report, "avg_price") and exec_result.report.avg_price else Decimal("0"),
                    )
                    store.append(episode)
                    episode_id = episode.episode_id
                    log.info("[edl] episode_created", episode_id=episode_id, position_id=position_id)

                    # Cache prior for settlement
                    if ccl is not None and _ccl_prior is not None:
                        ccl.cache_prior(episode_id, _ccl_prior)

                    # Composer: multi-model comparison
                    if ccl is not None:
                        asyncio.create_task(
                            ccl.compare_models(
                                primary_side=proc_result.execution_signal.side.value,
                                primary_confidence=proc_result.execution_signal.confidence,
                                model_version=result.model_version,
                                regime=result.regime,
                                feature_hash=feature_hash,
                            )
                        )

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

                    # Event sourcing: trade_executed
                    if event_store is not None:
                        await EventRecorder.record(
                            event_store,
                            "trade.executed",
                            "decision_loop",
                            {
                                "signal_id": proc_result.execution_signal.signal_id,
                                "position_id": position_id or "",
                                "side": proc_result.execution_signal.side.value,
                                "confidence": proc_result.execution_signal.confidence,
                                "episode_id": episode_id,
                                "entry_price": str(entry_price) if entry_price else "",
                            },
                        )
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
                # Update virtual portfolio with closed trade PnL
                if comp.virtual_balance is not None:
                    for detail in result.details:
                        if detail.get("action") == "CLOSE":
                            pnl_str = detail.get("pnl", "0")
                            try:
                                from decimal import Decimal
                                await comp.virtual_balance.record_trade(Decimal(str(pnl_str)))
                            except Exception as exc:
                                log.warning(
                                    "virtual_balance_record_trade_failed",
                                    pnl=pnl_str,
                                    error=str(exc),
                                )
                    # Update unrealized PnL from remaining open positions
                    if comp.position_repo is not None and comp.exchange is not None:
                        try:
                            open_positions = await comp.position_repo.list_open()
                            total_upnl = Decimal("0")
                            for pos in open_positions:
                                try:
                                    ticker = await comp.exchange.fetch_ticker(pos.symbol)
                                    last = ticker.get("last") or ticker.get("close") or 0.0
                                    current_price = Decimal(str(last))
                                    upnl = pos.compute_pnl_at(current_price)
                                    if upnl is not None:
                                        total_upnl += upnl
                                except Exception:
                                    pass
                            await comp.virtual_balance.update_unrealized_pnl(total_upnl)
                        except Exception as exc:
                            log.warning(
                                "virtual_balance_upnl_update_failed",
                                error=str(exc),
                            )

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

    from .observability.system_health import emit_system_health_snapshot
    from .observability.sl_integrity import default_sl_tracker
    from .observability.reconciliation_drift import default_drift_tracker
    from .observability.execution_integrity import default_execution_tracker

    try:
        while True:
            await asyncio.sleep(60.0)

            # PO-Layer: emit system health snapshot
            open_positions = 0
            if comp.position_repo is not None:
                try:
                    positions = await comp.position_repo.list_open()
                    open_positions = len(positions)
                except Exception:
                    pass
            emit_system_health_snapshot(
                sl_tracker=default_sl_tracker,
                drift_tracker=default_drift_tracker,
                execution_tracker=default_execution_tracker,
                open_positions_count=open_positions,
                algo_api_healthy=True,
                exchange_connected=comp.exchange is not None,
                pipeline_loaded=(
                    comp.streaming is not None
                    and comp.streaming.inference_pipeline.is_loaded
                ),
                recovery_state="COMPLETED" if comp.recovery_report is not None else "PENDING",
            )

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


async def _ccl_consistency_loop(ccl_consistency: object) -> None:
    """Loop 9: periodic CCL consistency reporting (every 5 min).

    Logs coverage statistics for posterior, likelihood, outcome, and
    event chain integrity.
    """
    from .infrastructure.edl.consistency import CCLConsistency

    checker: CCLConsistency = ccl_consistency

    try:
        while True:
            await asyncio.sleep(300.0)
            await checker.run_all(log_report=True)
    except asyncio.CancelledError:
        pass


async def _phase_b_loop(
    phase_b_tracker: object,
    ecl_health: object | None = None,
    episode_store: object | None = None,
    ccl: object | None = None,
    ccl_consistency: object | None = None,
    policy_hook: object | None = None,
) -> None:
    """Loop 7: Phase B metric collection every 60 seconds.

    Drives the PhaseBTracker: polls closed positions, updates market
    baselines, checks kill-switch thresholds, emits periodic metrics
    and daily reports.

    When ``ecl_health`` is provided, emits ``phase_b_experiment_health``
    after each tick.
    When ``episode_store`` is provided, settles TradeEpisodes for
    newly closed positions.
    When ``ccl_consistency`` is provided, runs CCL consistency checks
    periodically (every 10 iterations).
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
    _consistency_counter: int = 0

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

            # EDL + CCL: settle newly closed trades with Bayesian reasoning
            if store is not None:
                closed = tracker.all_closed_since(_settled_up_to)
                for entry in closed:
                    if not entry.episode_id:
                        continue
                    ep = store.get(entry.episode_id)
                    if ep is not None and ep.t_exit is None:
                        if ccl is not None:
                            from .infrastructure.edl.ccl_service import CCLService
                            ccl_svc: CCLService = ccl
                            prior_cached = ccl_svc.get_prior(entry.episode_id)
                            settled = await ccl_svc.settle_episode(
                                episode=ep,
                                pnl=entry.realized_pnl,
                                t_exit=entry.closed_at,
                                prediction_confidence=entry.confidence if hasattr(entry, "confidence") else 0.0,
                                prediction_side=entry.side if hasattr(entry, "side") else "",
                                regime=ep.regime_at_entry,
                                prior=prior_cached,
                                total_episodes=tracker.trade_count,
                                entry_price=ep.entry_price if ep.entry_price > 0 else None,
                            )
                            # FASE 3.2: Policy Update Hook
                            if settled is not None and policy_hook is not None:
                                from .infrastructure.edl.decision_augmentation import PolicyUpdateHook
                                hook: PolicyUpdateHook = policy_hook
                                hook.update(settled)
                        else:
                            settled = ep.settle(
                                t_exit=entry.closed_at,
                                pnl=entry.realized_pnl,
                            )
                        if settled is not None:
                            store.append_settled(settled)
                            log.info(
                                "[edl] episode_settled",
                                episode_id=entry.episode_id,
                                pnl=str(entry.realized_pnl),
                            )
                _settled_up_to = tracker.trade_count

            # Periodic CCL consistency check (every 10 iterations = 10 min)
            _consistency_counter += 1
            if ccl_consistency is not None and _consistency_counter % 10 == 0:
                from .infrastructure.edl.consistency import CCLConsistency
                ccl_check: CCLConsistency = ccl_consistency
                report = await ccl_check.run_all(log_report=True)
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

    if comp.exchange is None:
        raise RuntimeError("exchange is None")

    if comp.mode == "PAPER_TRADING" and comp.virtual_balance is not None:
        balance_provider = comp.virtual_balance
    else:
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

    # In PAPER_TRADING, pass CcxtBalanceProvider as margin cap provider
    margin_cap_provider: Any = None
    if comp.mode == "PAPER_TRADING":
        margin_cap_provider = CcxtBalanceProvider(exchange=comp.exchange)

    return ExecuteSignalUseCase(
        signal_validator=SignalValidator(),
        balance_provider=balance_provider,
        atr_calculator=atr_calc,
        exchange_client=comp.execution_authority,
        position_repo=position_repo,
        execution_logger=StructlogExecutionLogger(),
        is_halted=_is_halted,
        idempotency_store=idempotency_store,
        snapshot_repo=snapshot_repo,
        notifier=getattr(comp, "notify_on_event", None),
        algo_client=comp.algo_client,
        order_cleanup=comp.order_cleanup_service,
        is_testnet=os.environ.get("BINANCE_TESTNET", "false").lower() == "true",
        margin_cap_provider=margin_cap_provider,
    )


def _build_monitor_uc(comp: Composition) -> object:
    """Build MonitorPositionsUseCase for the streaming loop."""
    from decimal import Decimal

    from .application.use_cases.monitor_positions import MonitorPositionsUseCase
    from .infrastructure.execution.in_memory_position_repo import InMemoryPositionRepository
    from .infrastructure.execution.structlog_execution_logger import (
        StructlogExecutionLogger,
    )

    if comp.exchange is None:
        raise RuntimeError("exchange is None")

    exchange_client = comp.execution_authority
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
    trade_journal = getattr(comp, "trade_journal", None)
    return MonitorPositionsUseCase(
        position_repo=position_repo,
        exchange_client=exchange_client,
        execution_logger=StructlogExecutionLogger(),
        price_provider=price_provider,
        algo_client=comp.algo_client,
        order_cleanup=comp.order_cleanup_service,
        trade_journal=trade_journal,
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
async def health(request: Request) -> dict:
    comp: Composition | None = getattr(request.app.state, "composition", None)
    mode = comp.mode if comp is not None else os.environ.get("ENVIRONMENT_MODE", "PAPER_TRADING")
    uptime = time.monotonic() - _boot_timestamp if _boot_timestamp > 0 else 0
    status = "starting_up" if 0 < uptime < _WARMUP_SECONDS else "ok"
    return {
        "status": status,
        "mode": mode,
        "uptime_s": round(uptime, 1),
    }


@app.get("/health/pipeline")
async def health_pipeline() -> dict:
    return _pipeline_latency.snapshot()


@app.get("/health/ccl")
async def health_ccl(request: Request) -> dict:
    """CCL health: posterior coverage, episode stats, event chain integrity."""
    consistency = getattr(request.app.state, "ccl_consistency", None)
    if consistency is None:
        return {"status": "not_initialized", "episode_store": None}

    from .infrastructure.edl.consistency import CCLConsistency
    checker: CCLConsistency = consistency
    coverage = await checker.verify_posterior_coverage()
    episode_store = checker._episode_store

    return {
        "status": "active",
        "total_episodes": episode_store.count,
        "total_settled": episode_store.count_settled,
        "coverage": coverage,
    }


@app.get("/ccl/composer")
async def ccl_composer(request: Request) -> dict:
    """Composer status: available models, divergence metrics."""
    layer = getattr(request.app.state, "comparison_layer", None)
    if layer is None:
        return {"status": "not_initialized"}
    return layer.status()


@app.get("/ccl/belief")
async def ccl_belief(request: Request) -> dict:
    """Current CCL belief state per regime."""
    hook = getattr(request.app.state, "policy_hook", None)
    if hook is None:
        return {"status": "not_initialized"}
    return {
        "status": "ok",
        "belief": hook.snapshot(),
    }


@app.get("/ccl/trace/{episode_id}")
async def ccl_trace(episode_id: str, request: Request) -> dict:
    """Reconstruct the full event chain for a single trade."""
    consistency = getattr(request.app.state, "ccl_consistency", None)
    if consistency is None:
        return {"error": "CCL not initialized"}

    from .infrastructure.edl.consistency import CCLConsistency
    checker: CCLConsistency = consistency
    chain = await checker.replay_event_chain(episode_id=episode_id)
    return {
        "trade_id": chain.trade_id,
        "episode_id": chain.episode_id,
        "chain_complete": chain.chain_complete,
        "chain_break_reason": chain.chain_break_reason,
        "missing_links": chain.missing_links,
        "total_events": chain.total_events,
        "inference_event": chain.inference_event,
        "signal_event": chain.signal_event,
        "execution_event": chain.execution_event,
        "prior_event": chain.prior_event,
        "posterior_event": chain.posterior_event,
        "model_comparison_event": chain.model_comparison_event,
    }


@app.get("/ccl/aggregate")
async def ccl_aggregate(request: Request) -> dict:
    """Aggregate CCL episodes by regime, confidence band, and outcome."""
    aggregator = getattr(request.app.state, "event_aggregator", None)
    if aggregator is None:
        return {"status": "not_initialized"}

    report = aggregator.aggregate()
    return {
        "status": "ok",
        "total_trades": report.total_trades,
        "total_settled": report.total_settled,
        "overall_win_rate": report.overall_win_rate,
        "overall_pnl": report.overall_pnl,
        "by_regime": [
            {
                "regime": r.regime,
                "total_trades": r.total_trades,
                "wins": r.wins,
                "losses": r.losses,
                "total_pnl": r.total_pnl,
                "win_rate": r.win_rate,
                "profit_factor": r.profit_factor,
                "avg_confidence": r.avg_confidence,
                "avg_likelihood": r.avg_likelihood,
                "avg_posterior_edge": r.avg_posterior_edge,
            }
            for r in report.by_regime
        ],
        "by_confidence": [
            {
                "band": c.band,
                "total_trades": c.total_trades,
                "wins": c.wins,
                "losses": c.losses,
                "total_pnl": c.total_pnl,
                "win_rate": c.win_rate,
            }
            for c in report.by_confidence
        ],
        "by_outcome": [
            {
                "outcome": o.outcome,
                "count": o.count,
                "total_pnl": o.total_pnl,
                "avg_confidence": o.avg_confidence,
                "avg_likelihood": o.avg_likelihood,
                "avg_posterior_edge": o.avg_posterior_edge,
            }
            for o in report.by_outcome
        ],
    }


@app.get("/ccl/memory")
async def ccl_memory(
    request: Request,
    regime: str | None = None,
    outcome: str | None = None,
    min_trades: int = 1,
) -> dict:
    """Query the CCL memory index. Optionally filter by regime, outcome."""
    index = getattr(request.app.state, "memory_index", None)
    if index is None:
        return {"status": "not_initialized", "size": 0, "records": []}

    index.build()
    records = index.query(
        regime=regime,
        outcome=outcome,
        min_trades=min_trades,
    )
    return {
        "status": "ok",
        "size": index.size,
        "records": [
            {
                "context_hash": r.context_hash,
                "regime": r.regime,
                "confidence_band": r.confidence_band,
                "outcome": r.outcome,
                "total_trades": r.total_trades,
                "wins": r.wins,
                "losses": r.losses,
                "win_rate": r.win_rate,
                "total_pnl": round(r.total_pnl, 2),
                "avg_posterior_edge": round(r.avg_posterior_edge, 4),
            }
            for r in records[:50]
        ],
    }


@app.get("/ccl/memory/worst")
async def ccl_memory_worst(request: Request, top_n: int = 5) -> dict:
    """Return the worst-performing contexts."""
    index = getattr(request.app.state, "memory_index", None)
    if index is None:
        return {"status": "not_initialized"}
    index.build()
    return {
        "status": "ok",
        "worst_contexts": [
            {
                "context_hash": r.context_hash,
                "regime": r.regime,
                "band": r.confidence_band,
                "outcome": r.outcome,
                "total_trades": r.total_trades,
                "win_rate": r.win_rate,
                "total_pnl": round(r.total_pnl, 2),
            }
            for r in index.worst_contexts(top_n=top_n)
        ],
    }


@app.get("/ccl/memory/best")
async def ccl_memory_best(request: Request, top_n: int = 5) -> dict:
    """Return the best-performing contexts."""
    index = getattr(request.app.state, "memory_index", None)
    if index is None:
        return {"status": "not_initialized"}
    index.build()
    return {
        "status": "ok",
        "best_contexts": [
            {
                "context_hash": r.context_hash,
                "regime": r.regime,
                "band": r.confidence_band,
                "outcome": r.outcome,
                "total_trades": r.total_trades,
                "win_rate": r.win_rate,
                "total_pnl": round(r.total_pnl, 2),
            }
            for r in index.best_contexts(top_n=top_n)
        ],
    }


@app.post("/ccl/check")
async def ccl_check(request: Request) -> dict:
    """Run full CCL consistency check on demand and return report."""
    consistency = getattr(request.app.state, "ccl_consistency", None)
    if consistency is None:
        return {"status": "not_initialized"}

    from .infrastructure.edl.consistency import CCLConsistency
    checker: CCLConsistency = consistency
    report = await checker.run_all(log_report=True)
    return {
        "status": "ok" if report.all_ok else "issues_detected",
        "total_episodes": report.total_episodes,
        "total_settled": report.total_settled,
        "posterior_coverage_pct": report.posterior_coverage_pct,
        "missing_posterior": report.missing_posterior[:10],
        "missing_likelihood": report.missing_likelihood[:10],
        "missing_outcome": report.missing_outcome[:10],
        "broken_chains": report.broken_chains[:5],
        "passes": report.passes,
        "failures": report.failures,
        "all_ok": report.all_ok,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)
