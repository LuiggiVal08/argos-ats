"""Import Colab-trained checkpoints into the local repo.

Usage:
    python -m scripts.import_checkpoint --input checkpoints.zip

The ZIP must have the structure produced by the Colab notebook:

    {symbol_key}/
        v1.0.0/
            model_config.json
            model_metadata.json
            weights.keras
            xgboost_model.json       (optional)
            meta_model.json           (optional)
            calibrator.pkl            (optional)

Where {symbol_key} is like BTC_USDT, ETH_USDT, etc.
"""
from __future__ import annotations

import json
import tempfile
import zipfile
from argparse import ArgumentParser, Namespace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.infrastructure.models.checkpoint_repo_fs import (
    FsCheckpointRepository,
)
from app.domain.entities.nova_quant_model import NovaQuantModel
from app.domain.value_objects.model_config import ModelConfig


def _parse_args() -> Namespace:
    p = ArgumentParser(description="Import Colab-trained checkpoints")
    p.add_argument("--input", required=True, help="Checkpoint ZIP file")
    p.add_argument("--base-dir", default=None, help="Override repo base dir")
    return p.parse_args()


def _validate_entry(zf: zipfile.ZipFile, symbol_key: str, version: str) -> bool:
    required = [
        f"{symbol_key}/{version}/model_config.json",
        f"{symbol_key}/{version}/model_metadata.json",
        f"{symbol_key}/{version}/weights.keras",
    ]
    for path in required:
        if path not in zf.namelist():
            print(f"  WARNING: {symbol_key}/{version} missing {path}")
            return False
    return True


def _load_config(zf: zipfile.ZipFile, symbol_key: str, version: str) -> ModelConfig:
    data = json.loads(zf.read(f"{symbol_key}/{version}/model_config.json"))
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


def _load_metadata(zf: zipfile.ZipFile, symbol_key: str, version: str) -> dict[str, Any]:
    return json.loads(zf.read(f"{symbol_key}/{version}/model_metadata.json"))


def _load_weights(zf: zipfile.ZipFile, symbol_key: str, version: str) -> bytes:
    return zf.read(f"{symbol_key}/{version}/weights.keras")


def _save_extra_files(
    zf: zipfile.ZipFile,
    repo: FsCheckpointRepository,
    symbol_key: str,
    version: str,
    symbol: str,
) -> None:
    base = repo.base_dir / symbol_key / version
    extras = {
        "xgboost_model.json": "xgboost_model.json",
        "meta_model.json": "meta_model.json",
        "calibrator.pkl": "calibrator.pkl",
    }
    for zip_name, local_name in extras.items():
        path = f"{symbol_key}/{version}/{zip_name}"
        if path in zf.namelist():
            (base / local_name).write_bytes(zf.read(path))
            print(f"  -> saved {local_name}")


def main() -> None:
    args = _parse_args()
    repo = FsCheckpointRepository(base_dir=args.base_dir)

    with zipfile.ZipFile(args.input, "r") as zf:
        entries = set(zf.namelist())
        symbols = sorted(set(
            e.split("/")[0]
            for e in entries
            if e.count("/") >= 2
        ))

        if not symbols:
            print("No symbol directories found in ZIP")
            return

        print(f"Found symbols: {', '.join(symbols)}")

        for symbol_key in symbols:
            versions = sorted(set(
                e.split("/")[1]
                for e in entries
                if e.startswith(f"{symbol_key}/") and e.count("/") >= 2
            ), reverse=True)

            if not versions:
                continue

            version = versions[0]
            symbol = symbol_key.replace("_", "/")

            if not _validate_entry(zf, symbol_key, version):
                print(f"  SKIP {symbol_key} ({version}): incomplete")
                continue

            print(f"\nImporting {symbol} version {version}...")

            config = _load_config(zf, symbol_key, version)
            meta = _load_metadata(zf, symbol_key, version)
            weights = _load_weights(zf, symbol_key, version)

            model = NovaQuantModel(
                config=config,
                model_version=meta.get("model_version", version),
                trained_at=datetime.fromisoformat(meta["trained_at"]),
                weights_hash=meta["weights_hash"],
                feature_means=tuple(meta["feature_means"]),
                feature_stds=tuple(meta["feature_stds"]),
                metrics=meta.get("metrics", {}),
            )

            path = repo.save(model, weights, symbol=symbol)
            print(f"  -> saved to {path}")

            _save_extra_files(zf, repo, symbol_key, version, symbol)

    print("\nDone.")


if __name__ == "__main__":
    main()
