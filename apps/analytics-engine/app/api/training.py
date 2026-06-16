from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ..application.use_cases.register_model import RegisterModelUseCase
from ..application.use_cases.promote_model import PromoteModelUseCase
from ..application.use_cases.rollback_model import RollbackModelUseCase
from ..application.use_cases.list_model_versions import ListModelVersionsUseCase
from ..application.use_cases.compare_champion_challenger import (
    CompareChampionChallengerUseCase,
)
from ..application.use_cases.deploy_shadow import DeployShadowUseCase, ListShadowsUseCase
from ..application.use_cases.run_walk_forward import RunWalkForwardUseCase
from ..application.use_cases.compute_feature_importance import (
    ComputeFeatureImportanceUseCase,
)
from ..application.use_cases.ensemble_training import (
    EnsembleTrainingError,
    EnsembleTrainingUseCase,
)
from ..composition import (
    get_register_model_usecase,
    get_promote_model_usecase,
    get_rollback_model_usecase,
    get_list_models_usecase,
    get_compare_champion_challenger_usecase,
    get_deploy_shadow_usecase,
    get_list_shadows_usecase,
    get_walk_forward_usecase,
    get_feature_importance_usecase,
    get_ensemble_training_usecase,
)

router = APIRouter(prefix="/training", tags=["training"])


@router.post("/register", summary="Register a new model version")
async def register_model(
    request: Request,
    major: int,
    minor: int,
    metrics: dict[str, float],
    feature_names: list[str],
    hyperparams: dict[str, Any],
    dataset_id: str,
) -> dict:
    uc: RegisterModelUseCase = get_register_model_usecase(request)
    result = await uc.execute(
        major=major,
        minor=minor,
        metrics=metrics,
        feature_names=tuple(feature_names),
        hyperparams=hyperparams,
        dataset_id=dataset_id,
    )
    if not result.success:
        raise HTTPException(409, result.message)
    return {"version": result.version, "message": result.message}


@router.post("/promote", summary="Promote a challenger to champion")
async def promote_model(request: Request, version: str) -> dict:
    from ..domain.value_objects.model_version import ModelVersion
    uc: PromoteModelUseCase = get_promote_model_usecase(request)
    result = await uc.execute(ModelVersion.parse(version))
    return {
        "verdict": result.verdict,
        "champion_version": result.champion_version,
        "message": result.message,
    }


@router.post("/rollback", summary="Rollback to previous champion")
async def rollback_model(request: Request) -> dict:
    uc: RollbackModelUseCase = get_rollback_model_usecase(request)
    result = await uc.execute()
    return {
        "success": result.success,
        "previous_version": result.previous_version,
        "message": result.message,
    }


@router.get("/versions", summary="List all registered model versions")
async def list_versions(request: Request) -> list[dict]:
    uc: ListModelVersionsUseCase = get_list_models_usecase(request)
    return await uc.execute()


@router.post("/compare", summary="Compare champion vs challenger")
async def compare_champion_challenger(
    request: Request,
    champion: dict[str, Any],
    challenger: dict[str, Any],
) -> dict:
    from ..domain.entities.champion_challenger import ModelCandidate
    uc: CompareChampionChallengerUseCase = get_compare_champion_challenger_usecase(request)
    champ = ModelCandidate(**champion)
    chal = ModelCandidate(**challenger)
    result = await uc.execute(champ, chal)
    return {
        "verdict": result.verdict,
        "champion": result.champion_info,
        "challenger": result.challenger_info,
        "message": result.message,
    }


@router.post("/shadow/deploy", summary="Deploy a shadow model")
async def deploy_shadow(request: Request, model_id: str, version: str) -> dict:
    uc: DeployShadowUseCase = get_deploy_shadow_usecase(request)
    result = await uc.execute(model_id, version)
    return {
        "success": result.success,
        "model_id": model_id,
        "version": version,
        "message": result.message,
    }


@router.get("/shadow/list", summary="List all shadow deployments")
async def list_shadows(request: Request) -> list[dict]:
    uc: ListShadowsUseCase = get_list_shadows_usecase(request)
    shadows = await uc.execute()
    return [
        {
            "model_id": s.model_id,
            "version": s.version,
            "status": s.status.value,
            "total_predictions": s.total_predictions,
            "agreement_rate": s.agreement_rate(),
        }
        for s in shadows
    ]


@router.post("/walkforward", summary="Run walk-forward validation")
async def run_walk_forward(
    request: Request,
    n_samples: int,
    window_results: list[dict[str, float]],
) -> dict:
    uc: RunWalkForwardUseCase = get_walk_forward_usecase(request)
    result = await uc.execute(n_samples=n_samples, window_results=window_results)
    return {
        "verdict": result.verdict,
        "n_windows": result.n_windows,
        "passed_windows": result.passed_windows,
        "avg_metrics": result.avg_metrics,
        "fail_reason": result.fail_reason,
    }


@router.post("/feature-importance", summary="Compute feature importance")
async def feature_importance(
    request: Request,
    feature_names: list[str],
    X_sample: list[list[float]] | None = None,
) -> dict:
    uc: ComputeFeatureImportanceUseCase = get_feature_importance_usecase(request)
    result = await uc.execute(
        feature_names=feature_names,
        X_sample=X_sample,
    )
    return {
        "importance": result.importance,
        "total_features": result.total_features,
        "top_features": result.top_features,
    }


@router.post(
    "/train-ensemble",
    summary="Train full ensemble (LSTM + XGBoost + MetaModel) with Walk Forward.",
)
async def train_ensemble(
    request: Request,
    symbol: str = "BTC/USDT",
    timeframe: str = "1h",
    limit: int = 10000,
) -> dict:
    uc: EnsembleTrainingUseCase = get_ensemble_training_usecase(request)
    try:
        result = await uc.execute(
            symbol=symbol,
            timeframe=timeframe,
            limit=limit,
        )
    except EnsembleTrainingError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    return {
        "status": "ok",
        "lstm_version": result.lstm_version,
        "xgb_version": result.xgb_version,
        "meta_version": result.meta_version,
        "calibrator_version": result.calibrator_version,
        "n_windows": result.n_windows,
        "windows_passed": result.windows_passed,
        "avg_val_accuracy": result.avg_val_accuracy,
        "avg_val_loss": result.avg_val_loss,
        "oof_size": result.oof_size,
        "n_features": result.n_features,
        "meta_train_accuracy": result.meta_train_accuracy,
    }
