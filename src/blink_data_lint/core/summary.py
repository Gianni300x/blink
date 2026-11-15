"""Resumen compartido por la CLI y sus formatos; sin I/O ni códigos de proceso."""

from blink_data_lint.core.models import AnalysisReport, ComparisonReport, ReportSummary


def summarize(report: AnalysisReport, comparison: ComparisonReport | None = None) -> ReportSummary:
    warnings = sum(item.severity == "warning" for item in report.findings)
    skipped = sum(item.reason is not None for item in report.detectors)
    if comparison is not None:
        warnings += len(comparison.changes) + sum(item.flagged for item in comparison.drift)
        skipped += sum(item.reason is not None for item in comparison.drift)
    return ReportSummary(warnings, sum(item.severity == "info" for item in report.findings), skipped)
