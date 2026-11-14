"""Métricas exactas de salud y composición de detectores; sin I/O ni UI."""

import polars as pl

from blink.core.models import AnalysisReport, ColumnMetrics, Finding
from blink.core.detectors import detect

ID_MIN_VALUES = 10
ID_UNIQUE_RATIO = 0.95


def _percent(count: int, total: int) -> float | None:
    return count * 100 / total if total else None


def analyze(frame: pl.DataFrame, *, ml: bool = False) -> AnalysisReport:
    rows, width = frame.shape
    findings: list[Finding] = []
    columns: list[ColumnMetrics] = []
    if not rows or not width:
        findings.append(Finding("empty_dataset", "warning", "Dataset sin datos para evaluar."))

    # Una sola agregación columnar; no materializa filas en Python.
    expressions = []
    for index, (name, dtype) in enumerate(frame.schema.items()):
        column = pl.col(name)
        observed = column.drop_nulls()
        nan = column.is_nan().sum() if dtype.is_float() else pl.lit(0)
        if dtype.is_float():
            observed = observed.filter(~observed.is_nan())
        expressions.extend([
            column.null_count().alias(f"null_{index}"),
            nan.alias(f"nan_{index}"),
            observed.n_unique().alias(f"unique_{index}"),
        ])
        if dtype == pl.String:
            text = observed.filter(observed != "")
            expressions.extend([
                text.len().alias(f"text_{index}"),
                text.n_unique().alias(f"text_unique_{index}"),
            ])
    stats = frame.select(expressions).row(0, named=True) if width else {}

    for index, (name, dtype) in enumerate(frame.schema.items()):
        nulls = stats[f"null_{index}"]
        nans = stats[f"nan_{index}"]
        distinct = stats[f"unique_{index}"]
        all_null = rows > 0 and nulls == rows
        constant = distinct == 1
        text_count = stats.get(f"text_{index}", 0)
        text_unique = stats.get(f"text_unique_{index}", 0)
        possible_id = (
            text_count >= ID_MIN_VALUES and text_unique / text_count >= ID_UNIQUE_RATIO
        )
        columns.append(ColumnMetrics(
            name, str(dtype), nulls, _percent(nulls, rows), nans,
            distinct, constant, all_null, possible_id,
        ))
        if nulls:
            findings.append(Finding(
                "all_null" if all_null else "nulls", "warning",
                f"{nulls:,} nulos ({nulls / rows:.1%}).", name,
            ))
        if nans:
            findings.append(Finding("nan", "warning", f"{nans:,} valores NaN (no son null).", name))
        if constant:
            findings.append(Finding(
                "constant", "warning", "Un único valor observado; columna constante.", name,
            ))
        if possible_id:
            findings.append(Finding(
                "possible_id", "info",
                f"Posible ID: {text_unique:,}/{text_count:,} textos no vacíos únicos "
                f"({text_unique / text_count:.1%}).", name,
            ))

    duplicates = rows - frame.n_unique() if rows and width else 0
    if duplicates:
        findings.append(Finding(
            "duplicates", "warning",
            f"{duplicates:,} filas duplicadas excedentes ({duplicates / rows:.1%}).",
        ))
    null_count = sum(column.null_count for column in columns)
    detectors, detected_findings = detect(frame, ml=ml)
    findings.extend(detected_findings)
    return AnalysisReport(
        rows, tuple(columns), null_count, _percent(null_count, rows * width),
        sum(column.nan_count for column in columns), duplicates,
        _percent(duplicates, rows), tuple(findings),
        detectors=detectors,
    )
