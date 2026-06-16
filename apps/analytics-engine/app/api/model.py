"""NovaQuant model API: training and prediction endpoints.

POST /model/train         -> (deprecated) entrena modelo único LSTM
POST /model/predict       -> predice señal usando ensemble completo
GET  /model/info          -> información del modelo actual
GET  /model/features      -> análisis de correlación de features

Sad paths (todas retornan 422 con detalle en texto):
  - OhlcvSourceError: no se pudieron obtener velas
  - CheckpointNotFoundError: no hay modelo entrenado
  - StaleModelError: modelo vencido
  - PredictEnsembleError: fallo en pipeline ensemble
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..application.use_cases.predict_ensemble import (
    PredictEnsembleError,
    PredictEnsembleSignalUseCase,
)
from ..application.use_cases.predict_signal import (
    PredictSignalError,
)
from ..application.use_cases.train_model import (
    TrainModelError,
    TrainModelUseCase,
)
from ..composition import (
    get_model_use_cases,
    get_predict_ensemble_usecase,
)

router = APIRouter(prefix="/model", tags=["model"])


@router.post(
    "/train",
    summary="[deprecated] Train a single LSTM model. Use POST /training/train-ensemble instead.",
)
async def train_model(
    request: Request,
    symbol: str = "BTC/USDT",
    timeframe: str = "1h",
    limit: int = 5000,
) -> dict:
    use_cases = await get_model_use_cases(request)
    from fastapi.responses import JSONResponse
    try:
        result = await use_cases.train.execute(
            symbol=symbol,
            timeframe=timeframe,
            limit=limit,
        )
    except TrainModelError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    return JSONResponse(
        content={
            "status": "ok",
            "model_version": result.model.model_version,
            "features_used": list(result.features_used),
            "metrics": result.metrics,
            "train_samples": result.train_samples,
            "val_samples": result.val_samples,
            "test_samples": result.test_samples,
            "deprecated": True,
            "migration": "use POST /training/train-ensemble instead",
        },
        headers={"Warning": '299 - "POST /model/train is deprecated, use POST /training/train-ensemble"'},
    )


@router.post(
    "/predict",
    summary="Predict trading signal using full ensemble (LSTM + XGBoost).",
)
async def predict_signal(
    request: Request,
    symbol: str = "BTC/USDT",
    timeframe: str = "1h",
) -> dict:
    uc: PredictEnsembleSignalUseCase = get_predict_ensemble_usecase(request)
    try:
        result = await uc.execute(
            symbol=symbol,
            timeframe=timeframe,
        )
    except PredictEnsembleError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    return {
        "status": "ok",
        "signal": result.signal.side.value,
        "confidence": result.signal.confidence,
        "actionable": result.signal.is_actionable(),
        "probability_raw": result.probability_raw,
        "probability_calibrated": result.probability_calibrated,
        "uncertainty": result.uncertainty,
        "regime": result.regime,
        "adx": result.adx,
        "model_version": result.model_version,
        "lstm_buy": result.metadata.get("lstm_buy"),
        "lstm_sell": result.metadata.get("lstm_sell"),
        "lstm_hold": result.metadata.get("lstm_hold"),
        "xgb_buy": result.metadata.get("xgb_buy"),
        "xgb_sell": result.metadata.get("xgb_sell"),
        "xgb_hold": result.metadata.get("xgb_hold"),
    }


@router.get(
    "/info",
    summary="[deprecated] Get current model info. Will be removed — use ensemble pipeline diagnostics."
)
async def model_info(request: Request) -> dict:
    from fastapi.responses import JSONResponse
    use_cases = await get_model_use_cases(request)
    try:
        model = await use_cases.predict.load_model()
    except PredictSignalError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    return JSONResponse(
        content={
            "status": "ok",
            "model_version": model.model_version,
            "trained_at": model.trained_at.isoformat(),
            "age_days": round(model.age_days, 2),
            "is_stale": model.is_stale,
            "features": list(model.config.features),
            "lookback": model.config.lookback,
            "confidence_threshold": model.config.confidence_threshold,
            "metrics": model.metrics,
        },
        headers={"Warning": '299 - "GET /model/info is deprecated, ensemble diagnostics coming soon"'},
    )


@router.get(
    "/features",
    summary="[deprecated] Analyze feature correlations. Will be removed."
)
async def analyze_features(
    request: Request,
    symbol: str = "BTC/USDT",
    timeframe: str = "1h",
    limit: int = 1000,
) -> dict:
    from fastapi.responses import JSONResponse
    use_cases = await get_model_use_cases(request)
    try:
        result = await use_cases.train.execute(
            symbol=symbol,
            timeframe=timeframe,
            limit=limit,
        )
    except TrainModelError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    return JSONResponse(
        content={
            "status": "ok",
            "features_used": list(result.features_used),
            "correlations": result.correlations,
            "train_samples": result.train_samples,
        },
        headers={"Warning": '299 - "GET /model/features is deprecated, will be removed"'},
    )


@router.get(
    "/features",
    summary="Analyze feature correlations with the target.",
)
async def analyze_features(
    request: Request,
    symbol: str = "BTC/USDT",
    timeframe: str = "1h",
    limit: int = 1000,
) -> dict:
    use_cases = await get_model_use_cases(request)
    try:
        result = await use_cases.train.execute(
            symbol=symbol,
            timeframe=timeframe,
            limit=limit,
        )
    except TrainModelError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    return {
        "status": "ok",
        "features_used": list(result.features_used),
        "correlations": result.correlations,
        "train_samples": result.train_samples,
    }
