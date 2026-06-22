"""API routes (HTTP entry points)."""
from .backtest import router as backtest_router
from .circuit_breaker import router as circuit_breaker_router
from .dataset import router as dataset_router
from .execution import router as execution_router
from .incident import router as incident_router
from .model import router as model_router
from .notification import notification_router
from .order import router as order_router
from .observability import router as observability_router
from .risk import router as risk_router
from .shadow import router as shadow_router
from .training import router as training_router

__all__ = [
    "backtest_router",
    "circuit_breaker_router",
    "dataset_router",
    "execution_router",
    "incident_router",
    "model_router",
    "notification_router",
    "order_router",
    "observability_router",
    "risk_router",
    "shadow_router",
    "training_router",
]
