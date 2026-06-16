# mypy: disable-error-code="str-unpack,union-attr"
"""Argos analytics & IA engine entry point."""
import asyncio
import os
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
    training_router,
)
from .composition import Composition, build_composition
from .infrastructure.data.additional_data_consumer import AdditionalDataConsumer

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
app.include_router(observability_router)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Build the composition root once at startup. The risk router
    # fetches it via get_compute_position_size_usecase.
    comp: Composition = build_composition()
    app.state.composition = comp
    log.info("composition_built", mode=comp.mode, has_exchange=comp.exchange is not None)

    raw_symbol = os.environ.get("SYMBOL", "BTC/USDT")
    symbols = [s.strip() for s in os.environ.get("SYMBOLS", raw_symbol).split(",")]
    additional_consumer = AdditionalDataConsumer(symbols)
    await additional_consumer.start()
    app.state.additional_data_consumer = additional_consumer

    consumer_task = asyncio.create_task(_consume_ticks())
    try:
        yield
    finally:
        consumer_task.cancel()
        try:
            await consumer_task
        except asyncio.CancelledError:
            pass
        await additional_consumer.stop()
        # Close CCXT client on shutdown if LIVE/PAPER.
        if comp.exchange is not None:
            try:
                await comp.exchange.close()
            except Exception as e:  # noqa: BLE001
                log.warning("ccxt_close_error", error=str(e))


app.router.lifespan_context = lifespan


async def _consume_ticks() -> None:
    """H1 subscriber: pull every tick from the broker stream and log it.

    Real analytics (DataFrame updates, indicators) is added in later
    stories. For H1 we just prove end-to-end flow works.
    """
    url = os.environ.get("ARGOS_BROKER_URL", "redis://localhost:6379")
    stream = f"ticks:{os.environ.get('SYMBOL', 'BTC/USDT').replace('/', '').lower()}"
    last_id = "$"
    client = redis.from_url(url)
    log.info("subscriber_started", url=url, stream=stream)
    try:
        while True:
            try:
                res = await client.xread({"stream": last_id}, block=1000, count=100)
            except Exception as e:
                log.warning("xread_error", error=str(e))
                await asyncio.sleep(1)
                continue
            if not res:
                continue
            for _stream, entries in res:  # type: ignore
                for entry_id, fields in entries:  # type: ignore
                    last_id = (
                        entry_id.decode()  # type: ignore
                        if isinstance(entry_id, (bytes, bytearray))
                        else entry_id
                    )
                    # xread returns {field: value} as bytes by default
                    # (decode_responses=False). Decode on the fly.
                    decoded = {
                        (k.decode() if isinstance(k, (bytes, bytearray)) else k): (
                            v.decode() if isinstance(v, (bytes, bytearray)) else v
                        )
                        for k, v in fields  # type: ignore[misc]
                    }
                    payload = decoded.get("p")
                    if not payload:
                        continue
                    try:
                        import json

                        tick = json.loads(payload)
                        log.info(
                            "tick_received",
                            symbol=tick.get("symbol"),
                            trade_id=tick.get("tradeId"),
                            price_minor=tick.get("price", {}).get("minor"),
                            ts=tick.get("ts"),
                        )
                    except Exception as e:
                        log.warning("tick_parse_error", error=str(e))
    except asyncio.CancelledError:
        log.info("subscriber_cancelled")
        await client.aclose()
        raise


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "mode": os.environ.get("ENVIRONMENT_MODE", "PAPER_TRADING"),
    }


@app.get("/features/additional")
async def additional_features() -> dict:
    consumer: AdditionalDataConsumer | None = getattr(
        app.state, "additional_data_consumer", None
    )
    if consumer is None:
        return {"status": "not_initialized"}
    raw_symbol = os.environ.get("SYMBOL", "BTC/USDT")
    features = await consumer.get_additional_features(raw_symbol)
    return {
        "symbol": raw_symbol,
        "features": features,
    }


@app.get("/features/additional/{symbol:path}")
async def additional_features_by_symbol(symbol: str) -> dict:
    consumer: AdditionalDataConsumer | None = getattr(
        app.state, "additional_data_consumer", None
    )
    if consumer is None:
        return {"status": "not_initialized"}
    features = await consumer.get_additional_features(symbol)
    return {
        "symbol": symbol,
        "features": features,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)
