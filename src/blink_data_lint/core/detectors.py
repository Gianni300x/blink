"""Detectores de solo lectura; cobertura y omisiones explícitas."""

import math

import polars as pl

from blink_data_lint.core.models import DetectorResult, Finding
from blink_data_lint.core.roles import code_like

MIN_VALUES = 10
TYPE_RATIO = 0.9
ML_ROWS = 10_000
ML_COLUMNS = 32
SEED = 42


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
    count = ((normalized < low) | (normalized > high)).sum()
    base.update(evaluated_rows=len(values), count=count)
    return DetectorResult(**base, parameters=(
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
    base.update(evaluated_rows=len(values), count=len(values) - compatible)
    return DetectorResult(**base, parameters=(
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
    eligible = frame.select(columns).filter(pl.all_horizontal(
        pl.col(name).is_finite().fill_null(False) for name in columns
    ))
    base.update(eligible_rows=eligible.height)
    if eligible.height < 32:
        return DetectorResult(**base, reason="Menos de 32 filas completas y finitas.")
    sample = eligible.sample(n=ML_ROWS, seed=SEED, shuffle=True) if eligible.height > ML_ROWS else eligible
    # Solo la muestra acotada cruza a NumPy. sklearn no se importa al arrancar.
    import numpy as np
    from sklearn.ensemble import IsolationForest

    matrix = sample.to_numpy().astype(np.float64)
    # sklearn convierte internamente a float32; escalar evita overflow.
    matrix /= np.maximum(np.abs(matrix).max(axis=0), 1)
    if not np.any(np.ptp(matrix.astype(np.float32), axis=0) > 0):
        return DetectorResult(**base, reason="Sin variación representable en la muestra float32.")
    model = IsolationForest(n_estimators=64, max_samples=min(256, sample.height), contamination="auto",
                            random_state=SEED, n_jobs=1)
    count = int((model.fit_predict(matrix) == -1).sum())
    base.update(evaluated_rows=sample.height, count=count)
    return DetectorResult(**base, parameters=(
        ("seed", str(SEED)), ("árboles", "64"), ("max_samples", str(min(256, sample.height))),
        ("límite de filas", str(ML_ROWS)), ("límite de columnas", str(ML_COLUMNS)),
        ("contamination", "auto"), ("umbral decision_function", "<0"), ("n_jobs", "1"),
    ))


def detect(frame: pl.DataFrame, *, ml: bool = False) -> tuple[tuple[DetectorResult, ...], tuple[Finding, ...]]:
    results = []
    findings = []
    for name, dtype in frame.schema.items():
        if dtype.is_integer() or dtype.is_float():
            results.append(numeric_outliers(frame[name]))
        elif dtype == pl.String:
            results.append(broken_type(frame[name]))
    results.append(isolation_forest(frame, enabled=ml))
    for result in results:
        if result.count:
            code = {"IQR": "numeric_outliers", "tipo textual": "broken_type",
                    "IsolationForest": "isolation_outliers"}[result.method]
            findings.append(Finding(
                code, "warning", f"{result.count:,} indicios por {result.method} "
                f"sobre {result.evaluated_rows:,} filas evaluadas; revisar contexto.",
                result.columns[0] if len(result.columns) == 1 else None,
            ))
    return tuple(results), tuple(findings)
