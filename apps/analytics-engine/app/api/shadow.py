"""Shadow API: shadow mode metrics endpoint.

GET /shadow/metrics — returns expectancy, accuracy, profit factor,
regime stability, and drift from shadow outcomes.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/shadow", tags=["shadow"])


@router.get("/metrics")
async def shadow_metrics(request: Request) -> dict:
    client = getattr(request.app.state, "redis_client", None)
    if client is None:
        return {"error": "shadow_mode_unavailable", "detail": "no redis client (backtesting?)"}

    keys = await client.keys("shadow:outcome:*")
    if not keys:
        return {"error": "no_outcomes_yet", "total": 0}

    outcomes = []
    for k in keys:
        raw = await client.get(k)
        if raw:
            try:
                outcomes.append(json.loads(raw))
            except json.JSONDecodeError:
                continue

    from ..infrastructure.shadow.shadow_metrics import compute_shadow_metrics

    return compute_shadow_metrics(outcomes)
