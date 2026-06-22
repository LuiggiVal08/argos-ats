"""Checkpoint writer — atomic periodic snapshot to local file.

Writes a JSON checkpoint every N seconds using tmp + rename for
crash-safe atomicity. Non-blocking via asyncio.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

CheckpointData = dict[str, Any]
DataProvider = Callable[[], CheckpointData]


class CheckpointWriter:
    """Writes periodic checkpoints to a local file with atomic tmp+rename."""

    def __init__(
        self,
        file_path: str | None = None,
        interval_seconds: int = 30,
    ) -> None:
        self._path = Path(
            file_path or os.environ.get(
                "CHECKPOINT_PATH",
                str(Path.cwd() / "state" / "checkpoint.ae.json"),
            )
        )
        self._interval = interval_seconds
        self._task: asyncio.Task | None = None
        self._provider: DataProvider | None = None

    async def write(self, data: CheckpointData) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)

        tmp_path = self._path.with_suffix(".json.tmp")
        try:
            tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp_path.replace(self._path)
        except OSError:
            log.warning("checkpoint_write_failed", path=str(self._path))

    async def start(self, provider: DataProvider) -> None:
        if self._task is not None:
            return
        self._provider = provider
        self._task = asyncio.create_task(self._loop())
        log.info("checkpoint_writer_started", path=str(self._path), interval=self._interval)

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            if self._provider is not None:
                try:
                    await self.write(self._provider())
                except Exception:
                    log.warning("checkpoint_loop_error", exc_info=True)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        # Final flush
        if self._provider is not None:
            try:
                await self.write(self._provider())
            except Exception:
                log.warning("checkpoint_final_flush_failed", exc_info=True)
