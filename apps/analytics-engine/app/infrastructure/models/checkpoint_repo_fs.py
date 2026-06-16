"""Checkpoint repository: filesystem-based persistence.

Guarda checkpoints del modelo en disco local como:
  {base_dir}/
    v1.0.0/
      model_config.json     -> ModelConfig serializado
      model_metadata.json   -> NovaQuantModel metadata (version, metrics, means, stds)
      weights.keras         -> Pesos del modelo en formato Keras
    v1.0.1/
      ...

Tambien mantiene un symlink 'latest' que apunta a la version mas
reciente para carga rapida con load_latest().

Stack: pathlib, json.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from ...application.ports.checkpoint_repository import (
    CheckpointIOError,
    CheckpointNotFoundError,
    CheckpointRepository,
)
from ...domain.entities.nova_quant_model import (
    NovaQuantModel,
)
from ...domain.value_objects.model_config import ModelConfig

_DEFAULT_BASE_DIR = Path.home() / ".novaquant" / "checkpoints"


class FsCheckpointRepository:
    """Checkpoint repository en disco local.

    Uso:
        repo = FsCheckpointRepository(base_dir="/path/to/checkpoints")
        model, weights = await repo.load_latest()
        await repo.save(model, weights_bytes)
    """

    def __init__(self, base_dir: str | Path | None = None) -> None:
        self._base = Path(base_dir or _DEFAULT_BASE_DIR)
        self._base.mkdir(parents=True, exist_ok=True)

    async def save(
        self,
        model: NovaQuantModel,
        weights_bytes: bytes,
        symbol: str = "",
    ) -> str:
        """Persiste el modelo + pesos en disco.

        Args:
            model: entidad NovaQuantModel con metadatos.
            weights_bytes: pesos serializados del modelo Keras.
            symbol: trading pair (ej BTC/USDT) para multi-symbol.

        Returns:
            Ruta del directorio del checkpoint.

        Raises CheckpointIOError si falla la escritura.
        """
        base = self._base / _symbol_dir(symbol) if symbol else self._base
        version_dir = base / model.model_version
        try:
            version_dir.mkdir(parents=True, exist_ok=True)

            # Guardar config
            config_path = version_dir / "model_config.json"
            config_path.write_text(
                json.dumps(_config_to_dict(model.config), indent=2, default=str)
            )

            # Guardar metadata
            meta_path = version_dir / "model_metadata.json"
            meta_path.write_text(
                json.dumps(_metadata_to_dict(model), indent=2, default=str)
            )

            # Guardar pesos
            weights_path = version_dir / "weights.keras"
            weights_path.write_bytes(weights_bytes)

            # Actualizar symlink latest
            latest_base = self._base / _symbol_dir(symbol) if symbol else self._base
            latest_link = latest_base / "latest"
            if latest_link.exists() or latest_link.is_symlink():
                latest_link.unlink()
            latest_link.symlink_to(version_dir.name)

            return str(version_dir)

        except OSError as e:
            raise CheckpointIOError(
                f"failed to save checkpoint {model.model_version}: {e}"
            ) from e

    async def load_latest(
        self, symbol: str = "",
    ) -> tuple[NovaQuantModel, bytes]:
        """Carga el checkpoint mas reciente.

        Args:
            symbol: trading pair (ej BTC/USDT) para multi-symbol.

        Returns:
            (NovaQuantModel, weights_bytes).

        Raises CheckpointNotFoundError si no hay ninguno.
        """
        base = self._base / _symbol_dir(symbol) if symbol else self._base
        # Intentar symlink latest
        latest_link = base / "latest"
        if latest_link.exists():
            version_dir = latest_link.resolve()
        else:
            # Buscar el directorio mas reciente por nombre de version
            versions = sorted(
                [d for d in base.iterdir() if d.is_dir() and d.name != "latest"],
                reverse=True,
            )
            if not versions:
                raise CheckpointNotFoundError(
                    f"no checkpoints found in {base}"
                )
            version_dir = versions[0]

        return await self._load_from_dir(version_dir)

    async def load_version(
        self, model_version: str, symbol: str = "",
    ) -> tuple[NovaQuantModel, bytes]:
        """Carga un checkpoint por version.

        Args:
            model_version: version del modelo.
            symbol: trading pair (ej BTC/USDT) para multi-symbol.

        Raises CheckpointNotFoundError si no existe esa version.
        """
        base = self._base / _symbol_dir(symbol) if symbol else self._base
        version_dir = base / model_version
        if not version_dir.exists():
            raise CheckpointNotFoundError(
                f"checkpoint version {model_version} not found in {base}"
            )
        return await self._load_from_dir(version_dir)

    async def list_versions(self, symbol: str = "") -> list[str]:
        """Lista versiones disponibles ordenadas descendente.

        Args:
            symbol: trading pair (ej BTC/USDT) para multi-symbol.
        """
        base = self._base / _symbol_dir(symbol) if symbol else self._base
        versions = sorted(
            [
                d.name
                for d in base.iterdir()
                if d.is_dir() and d.name != "latest"
            ],
            reverse=True,
        )
        return versions

    # ── Privado ───────────────────────────────────────────────────

    async def _load_from_dir(
        self, version_dir: Path
    ) -> tuple[NovaQuantModel, bytes]:
        """Carga modelo + pesos desde un directorio de version."""
        try:
            config_path = version_dir / "model_config.json"
            meta_path = version_dir / "model_metadata.json"
            weights_path = version_dir / "weights.keras"

            if not all(p.exists() for p in [config_path, meta_path, weights_path]):
                raise CheckpointNotFoundError(
                    f"incomplete checkpoint in {version_dir}"
                )

            # Cargar config
            config_data = json.loads(config_path.read_text())
            config = _dict_to_config(config_data)

            # Cargar metadata
            meta = json.loads(meta_path.read_text())

            model = NovaQuantModel(
                config=config,
                model_version=meta["model_version"],
                trained_at=datetime.fromisoformat(meta["trained_at"]),
                weights_hash=meta["weights_hash"],
                feature_means=tuple(meta["feature_means"]),
                feature_stds=tuple(meta["feature_stds"]),
                metrics=meta.get("metrics", {}),
            )

            weights_bytes = weights_path.read_bytes()
            return model, weights_bytes

        except CheckpointNotFoundError:
            raise
        except OSError as e:
            raise CheckpointIOError(
                f"failed to load checkpoint from {version_dir}: {e}"
            ) from e

    @property
    def base_dir(self) -> Path:
        return self._base


# ── Funciones helpers de serializacion ─────────────────────────────


def _config_to_dict(config: ModelConfig) -> dict[str, Any]:
    return {
        "lookback": config.lookback,
        "confidence_threshold": config.confidence_threshold,
        "layers": list(config.layers),
        "features": list(config.features),
        "target_lookahead": config.target_lookahead,
        "target_return_pct": config.target_return_pct,
        "dropout_rate": config.dropout_rate,
        "batch_size": config.batch_size,
        "max_epochs": config.max_epochs,
        "early_stop_patience": config.early_stop_patience,
    }


def _dict_to_config(data: dict[str, Any]) -> ModelConfig:
    return ModelConfig(
        lookback=data["lookback"],
        confidence_threshold=data["confidence_threshold"],
        layers=tuple(data["layers"]),
        features=tuple(data["features"]),
        target_lookahead=data["target_lookahead"],
        target_return_pct=data["target_return_pct"],
        dropout_rate=data["dropout_rate"],
        batch_size=data["batch_size"],
        max_epochs=data["max_epochs"],
        early_stop_patience=data["early_stop_patience"],
    )


def _symbol_dir(symbol: str) -> Path:
    """Convierte BTC/USDT → BTC_USDT para nombres de directorio."""
    return Path(symbol.replace("/", "_"))


def _metadata_to_dict(model: NovaQuantModel) -> dict[str, Any]:
    return {
        "model_version": model.model_version,
        "trained_at": model.trained_at.isoformat(),
        "weights_hash": model.weights_hash,
        "feature_means": list(model.feature_means),
        "feature_stds": list(model.feature_stds),
        "metrics": model.metrics,
    }
