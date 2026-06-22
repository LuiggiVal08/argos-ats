"""Notification API endpoints: test event and health status."""
from __future__ import annotations

import structlog
from fastapi import APIRouter, Request

from ..application.use_cases.notify_on_event import NotifyOnEventUseCase
from ..composition import get_notify_on_event_usecase
from ..domain.value_objects.notification import (
    NotificationEvent,
    NotificationEventType,
    NotificationSeverity,
)

log = structlog.get_logger()
notification_router = APIRouter(prefix="/notification", tags=["notification"])


@notification_router.post("/test")
async def test_notification(request: Request) -> dict:
    notify = get_notify_on_event_usecase(request)
    event = NotificationEvent(
        event_type=NotificationEventType.TEST,
        severity=NotificationSeverity.INFO,
        title="Test Notification",
        message="This is a test message from argos-ats analytics engine.",
        symbol="BTC/USDT",
    )
    await notify.execute(event)
    log.info("test_notification_sent", event_type="test")
    return {"status": "ok", "event_type": event.event_type.value}


@notification_router.get("/status")
async def notification_status(request: Request) -> dict:
    notify = get_notify_on_event_usecase(request)
    return {"configured": True}
