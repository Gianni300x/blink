"""Contrato JSON versionado; sin Rich, lectura de archivos ni cálculos estadísticos."""

from dataclasses import asdict
import json

from blink.core.models import AnalysisReport, ComparisonReport, ReportSummary

SCHEMA_VERSION = "1.0"


def serialize_report(
    report: AnalysisReport, comparison: ComparisonReport | None, summary: ReportSummary,
    *, sources: dict, exit_code: int,
) -> str:
    analysis = asdict(report)
    analysis["detectors"] = [
        dict(asdict(item), parameters=dict(item.parameters),
             status="skipped" if item.reason is not None else "completed")
        for item in report.detectors
    ]
    compared = asdict(comparison) if comparison is not None else None
    if compared is not None:
        compared["drift"] = [
            dict(asdict(item), flagged=item.flagged,
                 status="skipped" if item.reason is not None else "completed")
            for item in comparison.drift
        ]
    return _encode(dict(
        schema_version=SCHEMA_VERSION, status="completed", sources=sources,
        analysis=analysis, comparison=compared, summary=asdict(summary),
        error=None, exit_code=exit_code,
    ))


def serialize_error(*, sources: dict, code: str, stage: str, message: str) -> str:
    return _encode(dict(
        schema_version=SCHEMA_VERSION, status="error", sources=sources,
        analysis=None, comparison=None, summary=None,
        error=dict(code=code, stage=stage, message=message), exit_code=2,
    ))


def _encode(document: dict) -> str:
    return json.dumps(document, ensure_ascii=True, allow_nan=False, indent=2)
