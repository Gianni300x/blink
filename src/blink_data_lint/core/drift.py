"""Comparación exacta de agregados; umbrales de revisión, no tests de hipótesis."""

import math

import polars as pl

from blink_data_lint.core.models import DriftMetric
from blink_data_lint.core.roles import code_like
from blink_data_lint.core.execution import collect
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


def _missing(previous: pl.Series, current: pl.Series) -> DriftMetric:
    if min(len(previous), len(current)) < MIN_ROWS:
        return _metric(previous, current, "missing_rate", len(previous), len(current),
                       threshold=MISSING_THRESHOLD, reason="Menos de 100 filas en alguno de los archivos.")

    def rate(series: pl.Series) -> float:
        missing = series.null_count()
        if series.dtype.is_float():
            missing += series.is_nan().sum()
        return missing / len(series)

    before, after = rate(previous), rate(current)
    return _metric(previous, current, "missing_rate", len(previous), len(current),
                   score=abs(after - before), threshold=MISSING_THRESHOLD,
                   previous_value=before, current_value=after)


def _numeric(previous: pl.Series, current: pl.Series) -> tuple[DriftMetric, ...]:
    before = previous.drop_nulls()
    before = before.filter(before.is_finite())
    after = current.drop_nulls()
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
    lost_precision = (np.unique(old_scaled).size != before.n_unique()
                      or np.unique(new_scaled).size != after.n_unique())
    old_mean, new_mean = float(old_scaled.mean()), float(new_scaled.mean())
    std = float(old_scaled.std(ddof=0))
    if lost_precision:
        mean = result("mean_shift", MEAN_THRESHOLD, reason="El escalado pierde valores distintos en float64.")
    elif std == 0:
        mean = result("mean_shift", MEAN_THRESHOLD, reason="Desviación del baseline cero o no representable.")
    else:
        score = abs(new_mean - old_mean) / std
        if not math.isfinite(score):
            mean = result("mean_shift", MEAN_THRESHOLD, reason="Desplazamiento de media no representable.")
        else:
            mean = result("mean_shift", MEAN_THRESHOLD, score=score,
                          previous_value=old_mean * scale, current_value=new_mean * scale)

    # Distancia empírica exacta: evaluar ambas CDF a la derecha de cada valor
    # observado contempla empates y tamaños distintos, sin p-valores ni muestreo.
    old_sorted, new_sorted = np.sort(old), np.sort(new)
    distance = 0.0
    # Recorrer todo el soporte en bloques, sin construir la unión ni dos CDF
    # completas. Es el mismo máximo exacto, no una muestra de observaciones.
    for values in (old_sorted, new_sorted):
        for offset in range(0, len(values), CDF_CHUNK_ROWS):
            support = np.unique(values[offset:offset + CDF_CHUNK_ROWS])
            delta = np.searchsorted(new_sorted, support, side="right") / len(new_sorted)
            delta -= np.searchsorted(old_sorted, support, side="right") / len(old_sorted)
            distance = max(distance, float(np.max(np.abs(delta))))
    return mean, result("ks", DISTRIBUTION_THRESHOLD, score=distance)


def _categorical(previous: pl.Series, current: pl.Series) -> DriftMetric:
    before, after = previous.drop_nulls(), current.drop_nulls()

    def result(**kwargs) -> DriftMetric:
        return _metric(previous, current, "total_variation", len(before), len(after),
                       threshold=DISTRIBUTION_THRESHOLD, **kwargs)

    if min(len(before), len(after)) < MIN_ROWS:
        return result(reason="Menos de 100 valores no nulos en alguno de los archivos.")
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


def detect_drift(current: pl.DataFrame | pl.LazyFrame, baseline: pl.DataFrame | pl.LazyFrame, *, progress: ProgressCallback | None = None) -> tuple[DriftMetric, ...]:
    results = []
    previous_schema = baseline.collect_schema() if isinstance(baseline, pl.LazyFrame) else baseline.schema
    schema = current.collect_schema() if isinstance(current, pl.LazyFrame) else current.schema
    notify(progress, "Comparando columnas", 0, len(schema))
    for index, (name, dtype) in enumerate(schema.items()):
        notify(progress, "Comparando columnas", index, len(schema))
        if name not in previous_schema:
            continue
        previous = collect(baseline.select(name)).to_series() if isinstance(baseline, pl.LazyFrame) else baseline[name]
        column = collect(current.select(name)).to_series() if isinstance(current, pl.LazyFrame) else current[name]
        if dtype != previous_schema[name]:
            results.append(_metric(previous, column, "distribution", 0, 0,
                                   reason="Tipos diferentes; no se convierten valores para comparar."))
            continue
        results.append(_missing(previous, column))
        if (dtype.is_integer() or dtype.is_float()) and code_like(name):
            results.append(_categorical(previous, column))
        elif dtype.is_integer() or dtype.is_float():
            results.extend(_numeric(previous, column))
        elif dtype in (pl.String, pl.Boolean):
            results.append(_categorical(previous, column))
        else:
            results.append(_metric(previous, column, "distribution", 0, 0,
                                   reason="Tipo sin comparación de distribución implementada."))
    notify(progress, "Comparando columnas", len(schema), len(schema))
    return tuple(results)
