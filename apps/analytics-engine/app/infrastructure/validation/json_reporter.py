"""JSON validation reporter."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from ...domain.entities.validation_report import ValidationReport


class JsonValidationReporter:
    """Persists validation report as JSON."""

    async def save(
        self,
        report: ValidationReport,
        output_dir: str,
        charts: dict[str, bytes] | None = None,
    ) -> str:
        os.makedirs(output_dir, exist_ok=True)
        path = os.path.join(output_dir, "validation_report.json")
        data = report.to_dict()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        return path
