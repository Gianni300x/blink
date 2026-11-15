"""Detectores de solo lectura; cobertura y omisiones explícitas."""

import math

import polars as pl

from blink_data_lint.core.models import DetectorResult, Finding
from blink_data_lint.core.roles import code_like
from blink_data_lint.core.progress import ProgressCallback, notify
from blink_data_lint.core.evidence import series_examples
from blink_data_lint.core.ml import ML_COLUMNS, isolation_lazy

MIN_VALUES = 10
TYPE_RATIO = 0.9


def numeric_outliers(series: pl.Series) -> DetectorResult:
    values = series.drop_nulls()
    values = values.filter(values.is_finite())
    base = dict(method="IQR", columns=(series.name,), total_rows=len(series),
                eligible_rows=len(values), evaluated_rows=0, count=None)
    if len(values) < MIN_VALUES:
        return DetectorResult(**base, reason="Menos de 10 valores finitos.")
    if code_like(series.name):
        return DetectorResult(**base, reason="Posible ID/código por nombre; no se aplica IQR a etiquetas.")
    # Escalado evita overflow al restar extremos finitos; las comparaciones
    # conservan el criterio IQR. Las cotas publicadas usan la escala original.
    scale = max(abs(values.min()), abs(values.max())) or 1
    normalized = values.cast(pl.Float64) / scale
    q1 = normalized.quantile(0.25, interpolation="linear")
    q3 = normalized.quantile(0.75, interpolation="linear")
    spread = q3 - q1
    if spread == 0:
        return DetectorResult(**base, reason="IQR cero; no se divide ni se infieren outliers.")
    low, high = q1 - 1.5 * spread, q3 + 1.5 * spread
    lower, upper = low * scale, high * scale
    if not math.isfinite(lower) or not math.isfinite(upper):
        return DetectorResult(**base, reason="Cotas IQR fuera del rango float64.")
    # Mantener las posiciones del archivo; no numerar la serie ya filtrada.
    original = series.cast(pl.Float64) / scale
    mask = series.is_finite().fill_null(False) & ((original < low) | (original > high))
    count = mask.sum()
    base.update(evaluated_rows=len(values), count=count)
    return DetectorResult(**base, examples=series_examples(series, mask), parameters=(
        ("factor", "1.5"), ("cuantiles", "lineales"),
        ("inferior", f"{lower:.12g}"), ("superior", f"{upper:.12g}"),
    ))


def broken_type(series: pl.Series) -> DetectorResult:
    values = series.drop_nulls().str.strip_chars()
    values = values.filter(values != "")
    base = dict(method="tipo textual", columns=(series.name,), total_rows=len(series),
                eligible_rows=len(values), evaluated_rows=0, count=None)
    if len(values) < MIN_VALUES:
        return DetectorResult(**base, reason="Menos de 10 textos no vacíos.")
    if code_like(series.name):
        return DetectorResult(**base, reason="Posible ID/código por nombre; no se infiere un tipo roto.")
    # No convertir la columna original: los valores incompatibles se conservan.
    numeric = values.cast(pl.Float64, strict=False)
    numeric_count = numeric.is_finite().fill_null(False).sum()
    leading_zero = values.str.contains(r"^[+-]?0\d+$").any()
    dates = values.str.contains(r"^\d{4}-\d{2}-\d{2}$") & (
        values.str.to_date(format="%Y-%m-%d", strict=False).is_not_null()
    )
    date_count = dates.sum()
    if numeric_count / len(values) >= TYPE_RATIO and not leading_zero:
        target, compatible = "numérico finito", numeric_count
    elif date_count / len(values) >= TYPE_RATIO:
        target, compatible = "fecha ISO YYYY-MM-DD", date_count
    else:
        reason = ("Posible código con ceros iniciales; conversión numérica omitida."
                  if leading_zero and numeric_count / len(values) >= TYPE_RATIO
                  else "Sin mayoría ≥90% numérica o de fechas ISO; no se adivina intención.")
        return DetectorResult(**base, reason=reason)
    original = series.str.strip_chars()
    present = original.is_not_null() & (original != "")
    if target == "numérico finito":
        valid = original.cast(pl.Float64, strict=False).is_finite().fill_null(False)
    else:
        valid = (original.str.contains(r"^\d{4}-\d{2}-\d{2}$") &
                 original.str.to_date(format="%Y-%m-%d", strict=False).is_not_null()).fill_null(False)
    base.update(evaluated_rows=len(values), count=len(values) - compatible)
    return DetectorResult(**base, examples=series_examples(series, present & ~valid), parameters=(
        ("tipo candidato", target), ("proporción mínima", "0.9"),
        ("compatibles", str(compatible)),
    ))


def isolation_forest(frame: pl.DataFrame, *, enabled: bool) -> DetectorResult:
    base = dict(method="IsolationForest", columns=(), total_rows=frame.height,
                eligible_rows=0, evaluated_rows=0, count=None)
    if not enabled:
        return DetectorResult(**base, reason="Desactivado; usar --ml para ejecutar.")
    columns = []
    for name, dtype in frame.schema.items():
        if not (dtype.is_integer() or dtype.is_float()):
            continue
        finite = frame[name].drop_nulls()
        finite = finite.filter(finite.is_finite())
        if len(finite) >= MIN_VALUES and finite.n_unique() > 1 and not code_like(name):
            columns.append(name)
    if len(columns) > ML_COLUMNS:
        return DetectorResult(**base, reason="Más de 32 columnas elegibles; trabajo ML acotado.")
    base.update(columns=tuple(columns))
    if not columns:
        return DetectorResult(**base, reason="Sin columnas numéricas variables elegibles.")
    return isolation_lazy(frame.lazy(), rows=frame.height, columns=columns)


def detect(frame: pl.DataFrame, *, ml: bool = False, progress: ProgressCallback | None = None) -> tuple[tuple[DetectorResult, ...], tuple[Finding, ...]]:
    results = []
    notify(progress, "Analizando columnas", 0, frame.width)
    for index, (name, dtype) in enumerate(frame.schema.items()):
        if dtype.is_integer() or dtype.is_float():
            results.append(numeric_outliers(frame[name]))
        elif dtype == pl.String:
            results.append(broken_type(frame[name]))
        notify(progress, "Analizando columnas", index + 1, frame.width)
    if ml:
        notify(progress, "Ejecutando ML")
    results.append(isolation_forest(frame, enabled=ml))
    return tuple(results), findings_for(results)


def findings_for(results) -> tuple[Finding, ...]:
    findings = []
    for result in results:
        if result.count:
            code = {"IQR": "numeric_outliers", "tipo textual": "broken_type",
                    "IsolationForest": "isolation_outliers"}[result.method]
            findings.append(Finding(
                code, "warning", f"{result.count:,} indicios por {result.method} "
                f"sobre {result.evaluated_rows:,} filas evaluadas; revisar contexto.",
                result.columns[0] if len(result.columns) == 1 else None, result.examples,
            ))
    return tuple(findings)
