"""Métricas exactas de salud y composición de detectores; sin I/O ni UI."""

from dataclasses import replace

import polars as pl

from blink_data_lint.core.execution import collect
from blink_data_lint.core.progress import ProgressCallback, notify
from blink_data_lint.core.evidence import finding_examples

from blink_data_lint.core.models import AnalysisReport, ColumnMetrics, Finding
from blink_data_lint.core.detectors import detect
from blink_data_lint.core.duplicates import excess_duplicates
from blink_data_lint.core.roles import code_like

ID_MIN_VALUES = 10
ID_UNIQUE_RATIO = 0.95


def _percent(count: int, total: int) -> float | None:
    return count * 100 / total if total else None


def analyze(frame: pl.DataFrame | pl.LazyFrame, *, ml: bool = False, progress: ProgressCallback | None = None, include_examples: bool = True) -> AnalysisReport:
    lazy = isinstance(frame, pl.LazyFrame)
    schema = frame.collect_schema() if lazy else frame.schema
    width = len(schema)
    rows = collect(frame.select(pl.len())).item() if lazy else frame.height
    findings: list[Finding] = []
    columns: list[ColumnMetrics] = []
    if not rows or not width:
        findings.append(Finding("empty_dataset", "warning", "Dataset sin datos para evaluar."))

    # En archivos, agregar una columna por vez reduce estados de únicos simultáneos.
    notify(progress, "Resumiendo columnas", 0, width)
    expressions = []
    stats = {}
    for index, (name, dtype) in enumerate(schema.items()):
        column_expressions = []
        column = pl.col(name)
        observed = column.drop_nulls()
        nan = column.is_nan().sum() if dtype.is_float() else pl.lit(0)
        if dtype.is_float():
            observed = observed.filter(~observed.is_nan())
        column_expressions.extend([
            column.null_count().alias(f"null_{index}"),
            nan.alias(f"nan_{index}"),
            observed.n_unique().alias(f"unique_{index}"),
        ])
        if dtype == pl.String:
            text = observed.filter(observed != "")
            column_expressions.extend([
                text.len().alias(f"text_{index}"),
                text.n_unique().alias(f"text_unique_{index}"),
            ])
        if lazy:
            stats.update(collect(frame.select(column_expressions)).row(0, named=True))
        else:
            expressions.extend(column_expressions)
        if lazy:
            notify(progress, "Resumiendo columnas", index + 1, width)
    if not lazy and width:
        stats = frame.select(expressions).row(0, named=True)
        notify(progress, "Resumiendo columnas", width, width)

    for index, (name, dtype) in enumerate(schema.items()):
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
        elif code_like(name):
            findings.append(Finding(
                "possible_code", "info",
                "Posible código/identificador por nombre; no se trata como medida numérica.", name,
            ))

    notify(progress, "Buscando duplicados")
    duplicates = excess_duplicates(frame)
    if duplicates:
        findings.append(Finding(
            "duplicates", "warning",
            f"{duplicates:,} filas duplicadas excedentes ({duplicates / rows:.1%}).",
        ))
    null_count = sum(column.null_count for column in columns)
    if include_examples:
        notify(progress, "Buscando ejemplos")
        source = frame if lazy else frame.lazy()
        findings = [replace(f, examples=finding_examples(source, f, schema)) for f in findings]
    if lazy:
        from blink_data_lint.core.streaming import detect_lazy
        detectors, detected_findings = detect_lazy(frame, rows=rows, metrics=columns, ml=ml, progress=progress, include_examples=include_examples)
    else:
        detectors, detected_findings = detect(frame, ml=ml, progress=progress, include_examples=include_examples)
    findings.extend(detected_findings)
    return AnalysisReport(
        rows, tuple(columns), null_count, _percent(null_count, rows * width),
        sum(column.nan_count for column in columns), duplicates,
        _percent(duplicates, rows), tuple(findings),
        detectors=detectors,
    )
