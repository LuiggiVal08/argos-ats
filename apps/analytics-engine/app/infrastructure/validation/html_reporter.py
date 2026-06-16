"""HTML validation reporter — autocontained report with embedded PNGs."""
from __future__ import annotations

import base64
import os
from datetime import datetime, timezone

from ...domain.entities.validation_report import (
    CheckStatus,
    CheckType,
    ValidationReport,
)


class HtmlValidationReporter:
    """Generates a self-contained HTML report with base64-embedded charts."""

    async def save(
        self,
        report: ValidationReport,
        output_dir: str,
        charts: dict[str, bytes],
    ) -> str:
        os.makedirs(output_dir, exist_ok=True)

        # Save individual PNGs
        for name, png_bytes in charts.items():
            path = os.path.join(output_dir, f"{name}.png")
            with open(path, "wb") as f:
                f.write(png_bytes)

        # Build HTML with embedded charts
        html = self._build_html(report, charts)
        html_path = os.path.join(output_dir, "validation_report.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)

        return html_path

    def _build_html(self, report: ValidationReport, charts: dict[str, bytes]) -> str:
        status_color = {
            CheckStatus.PASS: "#2ecc71",
            CheckStatus.WARNING: "#f39c12",
            CheckStatus.FAIL: "#e74c3c",
            CheckStatus.ERROR: "#95a5a6",
        }.get(report.status, "#95a5a6")

        checks_rows = "\n".join(self._check_row(c) for c in report.checks)
        charts_section = self._charts_section(charts)

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ARGOS 2.0 — Validation Report</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #1a1a2e; color: #e0e0e0; padding: 2rem; }}
.container {{ max-width: 1200px; margin: 0 auto; }}
.header {{ text-align: center; padding: 2rem 0; border-bottom: 2px solid {status_color}; margin-bottom: 2rem; }}
.header h1 {{ font-size: 2rem; color: #fff; }}
.header .status {{ display: inline-block; padding: 0.5rem 2rem; border-radius: 20px; background: {status_color}; color: #fff; font-weight: bold; font-size: 1.2rem; margin: 1rem 0; }}
.header .meta {{ color: #aaa; font-size: 0.9rem; }}
.section {{ background: #16213e; border-radius: 12px; padding: 1.5rem; margin-bottom: 1.5rem; }}
.section h2 {{ color: #fff; margin-bottom: 1rem; font-size: 1.3rem; }}
.check {{ display: flex; align-items: center; padding: 0.75rem; border-bottom: 1px solid #1a1a2e; }}
.check:last-child {{ border: none; }}
.check .dot {{ width: 12px; height: 12px; border-radius: 50%; margin-right: 1rem; flex-shrink: 0; }}
.check .name {{ flex: 1; font-weight: 500; }}
.check .message {{ color: #aaa; font-size: 0.85rem; margin-left: 1rem; }}
.chart-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(450px, 1fr)); gap: 1.5rem; }}
.chart {{ background: #0f3460; border-radius: 8px; padding: 1rem; }}
.chart h3 {{ color: #fff; font-size: 1rem; margin-bottom: 0.5rem; }}
.chart img {{ width: 100%; height: auto; border-radius: 4px; }}
.metrics-table {{ width: 100%; border-collapse: collapse; margin-top: 0.5rem; }}
.metrics-table th, .metrics-table td {{ padding: 0.5rem; text-align: left; border-bottom: 1px solid #1a1a2e; font-size: 0.9rem; }}
.metrics-table th {{ color: #aaa; font-weight: 500; }}
.warning-item {{ color: #f39c12; }}
.fail-item {{ color: #e74c3c; }}
@media (max-width: 768px) {{ .chart-grid {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
<div class="container">
<div class="header">
<h1>ARGOS 2.0 — Validation Report</h1>
<div class="status">{report.status.value}</div>
<div class="meta">
Symbol: {report.symbol} | Model: {report.model_version}<br>
Trained: {report.trained_at.strftime('%Y-%m-%d %H:%M UTC')} | Validated: {report.validated_at.strftime('%Y-%m-%d %H:%M UTC')}
</div>
</div>

<div class="section">
<h2>Overview</h2>
<table class="metrics-table">
<tr><th>Test Samples</th><td>{report.n_test_samples}</td><th>Features</th><td>{report.n_features}</td></tr>
<tr><th>Lookback</th><td>{report.lookback}</td><th>Windows</th><td>{report.n_windows}</td></tr>
</table>
</div>

<div class="section">
<h2>Checks</h2>
{checks_rows}
</div>

{f"<div class='section'><h2>Warnings</h2>{''.join(f'<div class=\"warning-item\">• {w}</div>' for w in report.warnings)}</div>" if report.warnings else ""}
{f"<div class='section'><h2>Critical Failures</h2>{''.join(f'<div class=\"fail-item\">• {f}</div>' for f in report.critical_failures)}</div>" if report.critical_failures else ""}

<div class="section">
<h2>Charts</h2>
<div class="chart-grid">
{charts_section}
</div>
</div>
</div>
</body>
</html>"""

    def _check_row(self, check) -> str:
        color_map = {
            CheckStatus.PASS: "#2ecc71",
            CheckStatus.WARNING: "#f39c12",
            CheckStatus.FAIL: "#e74c3c",
            CheckStatus.ERROR: "#95a5a6",
        }
        color = color_map.get(check.status, "#95a5a6")
        name = check.check_type.value.replace("_", " ").title()
        return (
            f'<div class="check">'
            f'<div class="dot" style="background:{color}"></div>'
            f'<div class="name">{name}</div>'
            f'<div class="message">{check.message}</div>'
            f'</div>'
        )

    def _charts_section(self, charts: dict[str, bytes]) -> str:
        titles = {
            "confusion_matrix": "Confusion Matrix",
            "probability_histogram": "Probability Distribution",
            "calibration_curve": "Calibration Curve",
            "feature_importance": "Feature Importance",
            "uncertainty_distribution": "MC Dropout Uncertainty",
        }
        rows = ""
        for name, png_bytes in charts.items():
            b64 = base64.b64encode(png_bytes).decode("utf-8")
            title = titles.get(name, name.replace("_", " ").title())
            rows += (
                f'<div class="chart">'
                f'<h3>{title}</h3>'
                f'<img src="data:image/png;base64,{b64}" alt="{title}">'
                f'</div>\n'
            )
        return rows
