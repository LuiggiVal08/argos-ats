"""Trade Episode — unidad atómica de evidencia del sistema EDL.

Contrato formal v1.0 (Evidence Contract Specification):

    E = (
        episode_id,
        t_entry, t_exit,
        side,
        pnl,
        regime_at_entry,
        model_version,
        feature_hash,
        feature_schema_version,
    )

Propiedades:
    - Inmutable (frozen dataclass).
    - feature_hash = SHA256(canonicalize(feature_vector)).
    - El vector crudo NO se almacena en el episodio (solo el hash).
    - Una vez emitido, no se recalcula ni reinterpreta.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

_FEATURE_SCHEMA_VERSION = "1.0"
_FEATURE_FLOAT_PRECISION = 6


# ── Feature Canonicalizer ──────────────────────────────────────────────────


class FeatureCanonicalizer:
    """Canonicalización determinista de vectores de features.

    Reglas:
        1. Ordenar features por nombre (lexicográfico).
        2. Redondear floats a ``_FEATURE_FLOAT_PRECISION`` decimales.
        3. NaN → null, Inf → null (eliminación determinista).
        4. Serializar como JSON compacto (sort_keys, separators).
        5. SHA256 del JSON resultante.
    """

    @staticmethod
    def canonicalize(
        features: dict[str, float],
        schema_version: str = _FEATURE_SCHEMA_VERSION,
    ) -> str:
        """Retorna el hash SHA256 del vector canónico.

        Args:
            features: Diccionario ``{feature_name: value}``.
            schema_version: Versión del schema de features.

        Returns:
            Hash hexadecimal de 64 caracteres.

        Raises:
            ValueError: Si el diccionario está vacío o contiene valores
                        no serializables.
        """
        if not features:
            raise ValueError("feature vector cannot be empty")

        cleaned: dict[str, Any] = {}
        for name in sorted(features.keys()):
            val = features[name]
            if val is None:
                cleaned[name] = None
            elif isinstance(val, float):
                if val != val or val == float("inf") or val == float("-inf"):
                    cleaned[name] = None
                else:
                    cleaned[name] = round(val, _FEATURE_FLOAT_PRECISION)
            elif isinstance(val, int):
                if val != val or val == float("inf") or val == float("-inf"):
                    cleaned[name] = None
                else:
                    cleaned[name] = round(float(val), _FEATURE_FLOAT_PRECISION)
            else:
                raise ValueError(
                    f"unsupported feature type for '{name}': {type(val).__name__}"
                )

        payload = {
            "schema_version": schema_version,
            "features": cleaned,
        }

        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ── Trade Episode ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TradeEpisode:
    """Episodio de trading — unidad atómica de evidencia.

    Una vez creado, SOLO se puede enriquecer vía ``settle()`` que
    produce un nuevo episodio con los campos de salida poblados.
    El episodio original permanece inmutable.
    """

    episode_id: str = field(default_factory=lambda: uuid4().hex[:12])

    t_entry: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    t_exit: datetime | None = None

    side: str = ""
    pnl: Decimal = Decimal("0")

    regime_at_entry: str = ""
    model_version: str = ""

    feature_hash: str = ""
    feature_schema_version: str = _FEATURE_SCHEMA_VERSION

    def settle(
        self,
        t_exit: datetime,
        pnl: Decimal,
    ) -> TradeEpisode:
        """Retorna un nuevo episodio con los campos de cierre poblados.

        El episodio original NO se modifica (frozen).
        """
        return TradeEpisode(
            episode_id=self.episode_id,
            t_entry=self.t_entry,
            t_exit=t_exit,
            side=self.side,
            pnl=pnl,
            regime_at_entry=self.regime_at_entry,
            model_version=self.model_version,
            feature_hash=self.feature_hash,
            feature_schema_version=self.feature_schema_version,
        )


# ── Trade Episode Store ────────────────────────────────────────────────────


class TradeEpisodeStore:
    """Almacén append-only de episodios.

    - No se puede eliminar un episodio.
    - No se puede modificar un episodio (solo ``settle()``).
    - El store mantiene una copia del episodio original y otra del
      settleado (si aplica), indexadas por episode_id.
    """

    def __init__(self) -> None:
        self._episodes: dict[str, TradeEpisode] = {}
        self._settled: dict[str, TradeEpisode] = {}

    def append(self, episode: TradeEpisode) -> None:
        if episode.episode_id in self._episodes:
            raise ValueError(
                f"episode {episode.episode_id} already exists"
            )
        if episode.t_exit is not None:
            raise ValueError(
                "cannot append a settled episode via append(); "
                "use append_settled() instead"
            )
        self._episodes[episode.episode_id] = episode

    def append_settled(self, episode: TradeEpisode) -> None:
        if episode.episode_id not in self._episodes:
            raise ValueError(
                f"cannot settle: episode {episode.episode_id} not found"
            )
        if episode.episode_id in self._settled:
            raise ValueError(
                f"episode {episode.episode_id} already settled"
            )
        if episode.t_exit is None:
            raise ValueError(
                f"cannot settle episode {episode.episode_id}: "
                "t_exit is None"
            )
        self._settled[episode.episode_id] = episode

    def get(self, episode_id: str) -> TradeEpisode | None:
        ep = self._episodes.get(episode_id)
        if ep is not None:
            return ep
        return self._settled.get(episode_id)

    def get_settled(self, episode_id: str) -> TradeEpisode | None:
        return self._settled.get(episode_id)

    @property
    def all_open(self) -> list[TradeEpisode]:
        settled_ids = set(self._settled.keys())
        return [
            ep for eid, ep in self._episodes.items()
            if eid not in settled_ids
        ]

    @property
    def all_settled(self) -> list[TradeEpisode]:
        return list(self._settled.values())

    @property
    def count(self) -> int:
        return len(self._episodes)

    @property
    def count_settled(self) -> int:
        return len(self._settled)
