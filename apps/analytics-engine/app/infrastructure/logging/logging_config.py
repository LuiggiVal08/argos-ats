"""Centralized logging configuration for ARGOS ATS.

Configures structlog with stdlib integration for JSON structured output
to rotating log files with 90-day retention and gzip compression.

Log files:
    logs/system.log       — generic system events, startup/shutdown
    logs/trades.log       — trade lifecycle (open, close, merge)
    logs/inference.log    — model inference per candle
    logs/orders.log       — order lifecycle (submit, fill, reject)
    logs/health.log       — periodic health metrics
    logs/errors.log       — ERROR and CRITICAL only (for alerting)
"""
from __future__ import annotations

import gzip
import logging
import os
import shutil
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any

import structlog

_LOG_DIR: Path | None = None
_LOGGERS: dict[str, structlog.stdlib.BoundLogger] = {}
_IS_CONFIGURED = False


def get_log_dir() -> Path:
    global _LOG_DIR
    if _LOG_DIR is None:
        raw = os.environ.get("ARGOS_LOG_DIR", "logs")
        _LOG_DIR = Path(raw).resolve()
        try:
            _LOG_DIR.mkdir(exist_ok=True, parents=True)
        except PermissionError:
            fallback = Path("/tmp/argos-logs")
            fallback.mkdir(exist_ok=True, parents=True)
            _LOG_DIR = fallback
    return _LOG_DIR


# ── Rotating file handler with gzip ──

class GzipRotatingFileHandler(TimedRotatingFileHandler):
    """TimedRotatingFileHandler that gzips rotated files on rollover."""

    def doRollover(self) -> None:
        super().doRollover()
        for path_str in self.getFilesToDelete():
            path = Path(path_str)
            if path.exists():
                gz_path = path.with_suffix(path.suffix + ".gz")
                try:
                    with open(path, "rb") as f_in:
                        with gzip.open(gz_path, "wb") as f_out:
                            shutil.copyfileobj(f_in, f_out)
                    path.unlink()
                except OSError:
                    pass


def _make_handler(
    name: str,
    level: int = logging.DEBUG,
    when: str = "midnight",
    backup_count: int = 90,
) -> GzipRotatingFileHandler:
    log_dir = get_log_dir()
    path = log_dir / f"{name}.log"
    handler = GzipRotatingFileHandler(
        filename=str(path),
        when=when,
        interval=1,
        backupCount=backup_count,
        encoding="utf-8",
        delay=True,
    )
    handler.setLevel(level)
    return handler


def _stdlib_formatter() -> logging.Formatter:
    return logging.Formatter(
        "%(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


def configure_logging(
    log_dir: str | Path | None = None,
    console_json: bool = True,
    level: int = logging.INFO,
) -> None:
    """Configure structlog with stdlib integration and rotating file handlers.

    Must be called once at application startup.

    Args:
        log_dir: Override log directory (default: ARGOS_LOG_DIR env or "logs").
        console_json: If True, emit JSON to stdout; else human-readable.
        level: Global log level filter (default: INFO).
    """
    global _IS_CONFIGURED
    if _IS_CONFIGURED:
        return

    if log_dir is not None:
        global _LOG_DIR
        _LOG_DIR = Path(log_dir)
        _LOG_DIR.mkdir(exist_ok=True, parents=True)

    # ── Stdlib handlers ──
    system_handler = _make_handler("system")
    trades_handler = _make_handler("trades")
    inference_handler = _make_handler("inference")
    orders_handler = _make_handler("orders")
    health_handler = _make_handler("health")
    errors_handler = _make_handler("errors", level=logging.ERROR)

    # ── Stdlib formatters ──
    fmt = _stdlib_formatter()
    for h in [system_handler, trades_handler, inference_handler,
              orders_handler, health_handler, errors_handler]:
        h.setFormatter(fmt)

    # ── Stdlib root logger ──
    root = logging.getLogger()
    root.setLevel(level)
    for h in [system_handler, errors_handler]:
        root.addHandler(h)

    # ── Named stdlib loggers ──
    _setup_stdlib_logger("system", system_handler, level)
    _setup_stdlib_logger("trades", trades_handler, level)
    _setup_stdlib_logger("inference", inference_handler, level, propagate=False)
    _setup_stdlib_logger("orders", orders_handler, level, propagate=False)
    _setup_stdlib_logger("health", health_handler, level, propagate=False)

    # ── Console output ──
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(fmt)
    root.addHandler(console)

    # ── structlog processors ──
    shared_processors: list[Any] = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    if console_json:
        console_processor: Any = structlog.processors.JSONRenderer()
    else:
        console_processor = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=shared_processors
        + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # ── structlog → stdlib processor formatters ──
    _setup_processor_formatter("system", shared_processors, console_json)
    _setup_processor_formatter("trades", shared_processors, console_json)
    _setup_processor_formatter("inference", shared_processors, console_json)
    _setup_processor_formatter("orders", shared_processors, console_json)
    _setup_processor_formatter("health", shared_processors, console_json)

    _IS_CONFIGURED = True


def _setup_stdlib_logger(
    name: str,
    handler: logging.Handler,
    level: int,
    propagate: bool = True,
) -> None:
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.addHandler(handler)
    logger.propagate = propagate


def _setup_processor_formatter(
    name: str,
    shared_processors: list[Any],
    console_json: bool,
) -> None:
    """Create a structlog ProcessorFormatter for the named stdlib logger."""
    renderer: Any = (
        structlog.processors.JSONRenderer()
        if console_json
        else structlog.dev.ConsoleRenderer()
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=shared_processors,
    )
    handlers = logging.getLogger(name).handlers
    if handlers:
        handlers[0].setFormatter(formatter)


def get_logger(
    name: str = "system",
    **initial: Any,
) -> structlog.stdlib.BoundLogger:
    """Get a bound logger for the given log category.

    Args:
        name: One of "system", "trades", "inference", "orders", "health".
        **initial: Key-value pairs to bind permanently to this logger.

    Returns:
        A structlog BoundLogger bound to the stdlib logger.
    """
    if not _IS_CONFIGURED:
        configure_logging()
    logger = structlog.get_logger(name)
    if initial:
        return logger.bind(**initial)
    return logger


def get_errors_logger() -> structlog.stdlib.BoundLogger:
    """Get the errors logger (captures ERROR+ across all categories)."""
    if not _IS_CONFIGURED:
        configure_logging()
    return structlog.get_logger()


def get_inference_logger(**initial: Any) -> structlog.stdlib.BoundLogger:
    return get_logger("inference", **initial)


def get_trades_logger(**initial: Any) -> structlog.stdlib.BoundLogger:
    return get_logger("trades", **initial)


def get_orders_logger(**initial: Any) -> structlog.stdlib.BoundLogger:
    return get_logger("orders", **initial)


def get_health_logger(**initial: Any) -> structlog.stdlib.BoundLogger:
    return get_logger("health", **initial)


def shutdown_logging() -> None:
    """Flush and close all log handlers."""
    logging.shutdown()
