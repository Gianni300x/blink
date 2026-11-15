"""Comparación de esquemas y agregados; sin lectura ni presentación."""

import polars as pl

from blink_data_lint.core.models import ComparisonReport, SchemaChange
from blink_data_lint.core.drift import detect_drift
from blink_data_lint.core.execution import collect
from blink_data_lint.core.progress import ProgressCallback


def compare(current: pl.DataFrame | pl.LazyFrame, baseline: pl.DataFrame | pl.LazyFrame, *, progress: ProgressCallback | None = None) -> ComparisonReport:
    changes = []
    previous_schema = baseline.collect_schema() if isinstance(baseline, pl.LazyFrame) else baseline.schema
    current_schema = current.collect_schema() if isinstance(current, pl.LazyFrame) else current.schema
    # Conservar el orden de las columnas, sin convertir tipos ni adivinar renombres.
    for name, dtype in current_schema.items():
        if name not in previous_schema:
            changes.append(SchemaChange(name, "added", None, str(dtype)))
        elif dtype != previous_schema[name]:
            changes.append(SchemaChange(name, "type_changed", str(previous_schema[name]), str(dtype)))
    for name, dtype in previous_schema.items():
        if name not in current_schema:
            changes.append(SchemaChange(name, "removed", str(dtype), None))
    return ComparisonReport(
        collect(baseline.select(pl.len())).item() if isinstance(baseline, pl.LazyFrame) else baseline.height,
        collect(current.select(pl.len())).item() if isinstance(current, pl.LazyFrame) else current.height,
        len(previous_schema), len(current_schema), tuple(changes),
        detect_drift(current, baseline, progress=progress),
    )
