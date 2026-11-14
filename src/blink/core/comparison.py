"""Comparación de esquemas por nombre; sin lectura, UI ni conversión de datos."""

import polars as pl

from blink.core.models import ComparisonReport, SchemaChange


def compare(current: pl.DataFrame, baseline: pl.DataFrame) -> ComparisonReport:
    changes = []
    previous_schema = baseline.schema
    current_schema = current.schema
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
        baseline.height, current.height, baseline.width, current.width, tuple(changes),
    )
