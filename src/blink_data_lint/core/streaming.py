"""Detectores por columna y muestra ML acotada antes de materializar filas."""

import polars as pl

from blink_data_lint.core.detectors import MIN_VALUES, numeric_outliers, broken_type, findings_for
from blink_data_lint.core.ml import isolation_lazy
from blink_data_lint.core.execution import collect
from blink_data_lint.core.models import DetectorResult
from blink_data_lint.core.roles import code_like, positional_index
from blink_data_lint.core.progress import ProgressCallback, notify


def detect_lazy(source: pl.LazyFrame, *, rows: int, metrics, ml: bool, progress: ProgressCallback | None = None):
    schema = source.collect_schema()
    results = []
    ml_columns = []
    indices = []
    finite = None
    notify(progress, "Analizando columnas", 0, len(metrics))
    for index, metric in enumerate(metrics):
        name, dtype = metric.name, schema[metric.name]
        if dtype.is_integer() or dtype.is_float():
            # Las columnas etiquetadas no necesitan materializarse para omitir IQR.
            if code_like(name):
                finite = collect(source.select(pl.col(name).is_finite().sum())).item()
                reason = "Menos de 10 valores finitos." if finite < MIN_VALUES else "Posible ID/código por nombre; no se aplica IQR a etiquetas."
                results.append(DetectorResult("IQR", (name,), rows, finite, 0, None, reason=reason))
            else:
                column = collect(source.select(name)).to_series()
                results.append(numeric_outliers(column))
                if ml and positional_index(column):
                    indices.append(name)
                elif ml and metric.distinct_count > 1:
                    finite = column.drop_nulls()
                    finite = finite.filter(finite.is_finite())
                    if len(finite) >= MIN_VALUES and finite.n_unique() > 1:
                        ml_columns.append(name)
                del column, finite
                finite = None
        elif dtype == pl.String:
            results.append(broken_type(collect(source.select(name)).to_series()))
        notify(progress, "Analizando columnas", index + 1, len(metrics))
    if ml:
        notify(progress, "Ejecutando ML")
        result = isolation_lazy(source, rows=rows, columns=ml_columns, excluded_indices=indices)
    else:
        result = DetectorResult("IsolationForest", (), rows, 0, 0, None,
                                reason="Desactivado; usar --ml para ejecutar.")
    results.append(result)
    return tuple(results), findings_for(results)
