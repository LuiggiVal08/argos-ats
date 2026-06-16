"""CheckpointRepository port.

Persiste y recupera checkpoints del modelo (pesos + metadatos).
El adapter concreto puede usar disco local, S3, o Redis.

Sad paths:
  - CheckpointNotFoundError: no hay checkpoint para cargar
  - CheckpointIOError: fallo de E/S al leer/escribir
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from ...domain.entities.nova_quant_model import NovaQuantModel


class CheckpointNotFoundError(RuntimeError):
    """Raised when no checkpoint matches the requested criteria."""


class CheckpointIOError(RuntimeError):
    """Raised when checkpoint can't be read or written."""


@runtime_checkable
class CheckpointRepository(Protocol):
    """Guarda y carga checkpoints del modelo."""

    async def save(
        self,
        model: NovaQuantModel,
        weights_bytes: bytes,
        symbol: str = "",
    ) -> str:
        """Persiste el modelo + pesos.

        Args:
            model: modelo entrenado.
            weights_bytes: pesos serializados.
            symbol: trading pair (opcional, para multi-symbol).

        Returns:
            Ruta o identificador del checkpoint guardado.

        Raises CheckpointIOError si falla.
        """
        ...

    async def load_latest(
        self, symbol: str = "",
    ) -> tuple[NovaQuantModel, bytes]:
        """Carga el checkpoint mas reciente.

        Args:
            symbol: trading pair (opcional, multi-symbol).

        Returns:
            (NovaQuantModel, weights_bytes).

        Raises CheckpointNotFoundError si no hay ninguno.
        Raises CheckpointIOError si falla la lectura.
        """
        ...

    async def load_version(
        self, model_version: str, symbol: str = "",
    ) -> tuple[NovaQuantModel, bytes]:
        """Carga un checkpoint por version.

        Args:
            model_version: version del modelo.
            symbol: trading pair (opcional, multi-symbol).

        Raises CheckpointNotFoundError si no existe esa version.
        """
        ...

    async def list_versions(self, symbol: str = "") -> list[str]:
        """Lista versiones disponibles ordenadas descendente.

        Args:
            symbol: trading pair (opcional, multi-symbol).
        """
        ...
