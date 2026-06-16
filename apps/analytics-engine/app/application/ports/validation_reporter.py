"""ValidationReporter port — output of validation reports."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from ...domain.entities.validation_report import ValidationReport


@runtime_checkable
class ValidationReporter(Protocol):
    """Persists validation results as HTML, JSON, and PNGs."""

    async def save(
        self,
        report: ValidationReport,
        output_dir: str,
        charts: dict[str, bytes],
    ) -> str:
        """Generate and persist validation report.

        Args:
            report: ValidationReport with all checks and metrics.
            output_dir: Base directory for reports.
            charts: dict of chart name → PNG bytes.

        Returns:
            Path to the generated HTML report.
        """
        ...
