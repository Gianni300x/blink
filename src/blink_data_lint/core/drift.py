"""Comparación exacta de agregados; umbrales de revisión, no tests de hipótesis."""

import math

import polars as pl
from polars.selectors import by_name

from blink_data_lint.core.models import DriftMetric
from blink_data_lint.core.roles import code_like
from blink_data_lint.core.execution import collect, count_rows
from blink_data_lint.core.progress import ProgressCallback, notify

MIN_ROWS = 100
MEAN_THRESHOLD = 0.5
DISTRIBUTION_THRESHOLD = 0.2
MISSING_THRESHOLD = 0.1
MAX_CATEGORIES = 50
CDF_CHUNK_ROWS = 65_536


def _metric(
    previous: pl.Series, current: pl.Series, method: str,
    previous_count: int, current_count: int, *, score: float | None = None,
    threshold: float | None = None, reason: str | None = None,
    previous_value: float | None = None, current_value: float | None = None,
) -> DriftMetric:
    return DriftMetric(current.name, method, len(previous), len(current),
                       previous_count, current_count, score, threshold, reason,
                       previous_value, current_value)


def _missing_metric(name: str, baseline_rows: int, current_rows: int,
                    before: float | None = None, after: float | None = None) -> DriftMetric:
    if before is None or after is None:
        return DriftMetric(name, "missing_rate", baseline_rows, current_rows, baseline_rows, current_rows,
                           None, MISSING_THRESHOLD, "Menos de 100 filas en alguno de los archivos.")
    return DriftMetric(name, "missing_rate", baseline_rows, current_rows, baseline_rows, current_rows,
                       abs(after - before), MISSING_THRESHOLD, None, before, after)


def _missing(previous: pl.Series, current: pl.Series) -> DriftMetric:
    if min(len(previous), len(current)) < MIN_ROWS:
        return _missing_metric(current.name, len(previous), len(current))

    def rate(series: pl.Series) -> float:
        missing = series.null_count()
        if series.dtype.is_float():
            missing += series.rename("value").is_nan().sum()
        return missing / len(series)

    before, after = rate(previous), rate(current)
    return _missing_metric(current.name, len(previous), len(current), before, after)


def _null_count(frame: pl.DataFrame | pl.LazyFrame, name: str) -> int:
    if isinstance(frame, pl.LazyFrame):
        return collect(frame.select(by_name(name).as_expr().null_count())).item()
    return frame[name].null_count()


def _ks_distance(old, new) -> float:
    import numpy as np

    old_sorted, new_sorted = np.sort(old), np.sort(new)
    distance = 0.0
    # La diferencia dirigida solo aumenta al saltar la CDF propia. La posición
    # final de cada grupo de empates ya da su conteo acumulado, incluso si el
    # grupo cruza varios bloques. Solo se busca el rango en la otra muestra.
    for values, other in ((old_sorted, new_sorted), (new_sorted, old_sorted)):
        last = len(values) - 1
        for offset in range(0, last, CDF_CHUNK_ROWS):
            end = min(offset + CDF_CHUNK_ROWS, last)
            ends = offset + np.flatnonzero(values[offset:end] != values[offset + 1:end + 1])
            if ends.size:
                delta = (ends + 1) / len(values)
                delta -= np.searchsorted(other, values[ends], side="right") / len(other)
                distance = max(distance, float(delta.max()))
        other_count = np.searchsorted(other, values[last], side="right")
        distance = max(distance, float(1 - other_count / len(other)))
    return distance


def _numeric(previous: pl.Series, current: pl.Series) -> tuple[DriftMetric, ...]:
    before = previous.rename("value").drop_nulls()
    before = before.filter(before.is_finite())
    after = current.rename("value").drop_nulls()
    after = after.filter(after.is_finite())

    def result(method: str, threshold: float, **kwargs) -> DriftMetric:
        return _metric(previous, current, method, len(before), len(after), threshold=threshold, **kwargs)

    def skipped(reason: str) -> tuple[DriftMetric, ...]:
        return (result("mean_shift", MEAN_THRESHOLD, reason=reason),
                result("ks", DISTRIBUTION_THRESHOLD, reason=reason))

    if min(len(before), len(after)) < MIN_ROWS:
        return skipped("Menos de 100 valores finitos en alguno de los archivos.")
    if current.dtype.is_integer():
        if any(max(abs(values.min()), abs(values.max())) > 2**53 for values in (before, after)):
            return skipped("Enteros fuera de ±2^53; float64 perdería precisión.")

    # NumPy solo materializa dos columnas finitas cuando se usa --baseline.
    import numpy as np

    old = before.cast(pl.Float64).to_numpy()
    new = after.cast(pl.Float64).to_numpy()
    scale = max(float(np.abs(old).max()), float(np.abs(new).max())) or 1.0
    old_scaled, new_scaled = old / scale, new / scale
    baseline_distinct = before.n_unique()
    lost_precision = (np.unique(old_scaled).size != baseline_distinct
                      or np.unique(new_scaled).size != after.n_unique())
    old_mean, new_mean = float(old_scaled.mean()), float(new_scaled.mean())
    std = float(old_scaled.std(ddof=0))
    if lost_precision:
        mean = result("mean_shift", MEAN_THRESHOLD, reason="El escalado pierde valores distintos en float64.")
    elif baseline_distinct == 1 or std == 0:
        mean = result("mean_shift", MEAN_THRESHOLD, reason="Desviación del baseline cero o no representable.")
    else:
        score = abs(new_mean - old_mean) / std
        if not math.isfinite(score):
            mean = result("mean_shift", MEAN_THRESHOLD, reason="Desplazamiento de media no representable.")
        else:
            mean = result("mean_shift", MEAN_THRESHOLD, score=score,
                          previous_value=old_mean * scale, current_value=new_mean * scale)

    return mean, result("ks", DISTRIBUTION_THRESHOLD, score=_ks_distance(old, new))


def _categorical(previous: pl.Series, current: pl.Series) -> DriftMetric:
    before = previous.rename("value").drop_nulls()
    after = current.rename("value").drop_nulls()
    if current.dtype.is_float():
        before, after = before.drop_nans(), after.drop_nans()

    def result(**kwargs) -> DriftMetric:
        return _metric(previous, current, "total_variation", len(before), len(after),
                       threshold=DISTRIBUTION_THRESHOLD, **kwargs)

    if min(len(before), len(after)) < MIN_ROWS:
        return result(reason="Menos de 100 valores sin faltantes en alguno de los archivos.")
    if pl.concat([before, after]).n_unique() > MAX_CATEGORIES:
        return result(reason="Más de 50 categorías; posible ID o texto libre.")
    # Solo se materializan los conteos de hasta 50 categorías en Python.
    def counts(values: pl.Series) -> dict:
        table = values.rename("value").value_counts(name="frequency")
        return dict(zip(table["value"].to_list(), table["frequency"].to_list()))

    old_counts, new_counts = counts(before), counts(after)
    categories = old_counts.keys() | new_counts.keys()
    distance = math.fsum(abs(new_counts.get(key, 0) / len(after) - old_counts.get(key, 0) / len(before))
                         for key in categories) / 2
    return result(score=distance)


def detect_drift(current: pl.DataFrame | pl.LazyFrame, baseline: pl.DataFrame | pl.LazyFrame, *,
                 progress: ProgressCallback | None = None,
                 row_counts: tuple[int, int] | None = None) -> tuple[DriftMetric, ...]:
    results = []
    previous_schema = baseline.collect_schema() if isinstance(baseline, pl.LazyFrame) else baseline.schema
    schema = current.collect_schema() if isinstance(current, pl.LazyFrame) else current.schema
    notify(progress, "Comparando columnas", 0, len(schema))
    for index, (name, dtype) in enumerate(schema.items()):
        notify(progress, "Comparando columnas", index, len(schema))
        if name not in previous_schema:
            continue
        numeric = dtype.is_integer() or dtype.is_float()
        type_changed = dtype != previous_schema[name]
        if type_changed or (not numeric and dtype not in (pl.String, pl.Boolean)):
            if row_counts is None:
                row_counts = count_rows(baseline), count_rows(current)
            baseline_rows, current_rows = row_counts
            if type_changed:
                reason = "Tipos diferentes; no se convierten valores para comparar."
            else:
                if min(baseline_rows, current_rows) >= MIN_ROWS:
                    before = _null_count(baseline, name) / baseline_rows
                    after = _null_count(current, name) / current_rows
                    results.append(_missing_metric(name, baseline_rows, current_rows, before, after))
                else:
                    results.append(_missing_metric(name, baseline_rows, current_rows))
                reason = "Tipo sin comparación de distribución implementada."
            results.append(DriftMetric(name, "distribution", baseline_rows, current_rows, 0, 0, None, None, reason))
            continue
        previous = collect(baseline.select(by_name(name))).to_series() if isinstance(baseline, pl.LazyFrame) else baseline[name]
        column = collect(current.select(by_name(name))).to_series() if isinstance(current, pl.LazyFrame) else current[name]
        results.append(_missing(previous, column))
        if numeric and not code_like(name):
            results.extend(_numeric(previous, column))
        else:
            results.append(_categorical(previous, column))
    notify(progress, "Comparando columnas", len(schema), len(schema))
    return tuple(results)
