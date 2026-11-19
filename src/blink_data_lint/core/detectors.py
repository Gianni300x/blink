"""Detectores de solo lectura; cobertura y omisiones explícitas."""

import math
import sys

import polars as pl

from blink_data_lint.core.models import DetectorResult, Finding
from blink_data_lint.core.roles import code_like, positional_index
from blink_data_lint.core.progress import ProgressCallback, notify
from blink_data_lint.core.evidence import series_examples
from blink_data_lint.core.ml import isolation_lazy

MIN_VALUES = 10
TYPE_RATIO = 0.9


def numeric_outliers(series: pl.Series, *, include_examples: bool = True) -> DetectorResult:
    # Algunas operaciones Series de Polars resuelven el nombre como expresión.
    measurements = series.rename("value")
    values = measurements.drop_nulls()
    values = values.filter(values.is_finite())
    base = dict(method="IQR", columns=(series.name,), total_rows=len(series),
                eligible_rows=len(values), evaluated_rows=0, count=None)
    if len(values) < MIN_VALUES:
        return DetectorResult(**base, reason="Menos de 10 valores finitos.")
    if code_like(series.name):
        return DetectorResult(**base, reason="Posible ID/código por nombre; no se aplica IQR a etiquetas.")
    # Escalar solo cuando las cotas podrían desbordar. Con magnitudes normales
    # la división previa puede mover un valor justo en la cota por redondeo.
    magnitude = max(abs(values.min()), abs(values.max()))
    if values.dtype.is_integer() and magnitude > 2**53:
        return DetectorResult(**base, reason="Enteros fuera de ±2^53; float64 podría perder precisión.")
    extreme = magnitude > sys.float_info.max / 4 or 0 < magnitude < sys.float_info.min * 4
    scale = magnitude if extreme else 1
    normalized = values.cast(pl.Float64)
    if scale != 1:
        normalized = normalized / scale
        if normalized.n_unique() != values.n_unique():
            return DetectorResult(**base, reason="El escalado pierde precisión entre valores distintos.")
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
    original = measurements.cast(pl.Float64) / scale
    mask = measurements.is_finite().fill_null(False) & ((original < low) | (original > high))
    count = mask.sum()
    base.update(evaluated_rows=len(values), count=count)
    return DetectorResult(**base, examples=series_examples(series, mask) if include_examples else (), parameters=(
        ("factor", "1.5"), ("cuantiles", "lineales"),
        ("inferior", f"{lower:.12g}"), ("superior", f"{upper:.12g}"),
    ))


AMBIGUOUS_COMMA_REASON = (
    "Posible coma decimal o separador de miles. La columna se conserva como texto. "
    "Si contiene medidas, exportá con punto decimal y sin separador de miles."
)


def broken_type(series: pl.Series, *, include_examples: bool = True) -> DetectorResult:
    text = series.rename("value")
    values = text.drop_nulls().str.strip_chars()
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
        comma_numbers = values.str.contains(r"^[+-]?[0-9]+,[0-9]+$").sum()
        if comma_numbers and not leading_zero and (numeric_count + comma_numbers) / len(values) >= TYPE_RATIO:
            return DetectorResult(**base, reason=AMBIGUOUS_COMMA_REASON)
        reason = ("Posible código con ceros iniciales; conversión numérica omitida."
                  if leading_zero and numeric_count / len(values) >= TYPE_RATIO
                  else "Sin mayoría ≥90% numérica o de fechas ISO; no se adivina intención.")
        return DetectorResult(**base, reason=reason)
    original = text.str.strip_chars()
    present = original.is_not_null() & (original != "")
    if target == "numérico finito":
        valid = original.cast(pl.Float64, strict=False).is_finite().fill_null(False)
    else:
        valid = (original.str.contains(r"^\d{4}-\d{2}-\d{2}$") &
                 original.str.to_date(format="%Y-%m-%d", strict=False).is_not_null()).fill_null(False)
    base.update(evaluated_rows=len(values), count=len(values) - compatible)
    return DetectorResult(**base, examples=series_examples(series, present & ~valid) if include_examples else (), parameters=(
        ("tipo candidato", target), ("proporción mínima", "0.9"),
        ("compatibles", str(compatible)),
    ))


def isolation_forest(frame: pl.DataFrame, *, enabled: bool, include_examples: bool = True) -> DetectorResult:
    base = dict(method="IsolationForest", columns=(), total_rows=frame.height,
                eligible_rows=0, evaluated_rows=0, count=None)
    if not enabled:
        return DetectorResult(**base, reason="Desactivado; usar --ml para ejecutar.")
    columns = []
    indices = []
    for name, dtype in frame.schema.items():
        if not (dtype.is_integer() or dtype.is_float()):
            continue
        if positional_index(frame[name]):
            indices.append(name)
            continue
        finite = frame[name].rename("value").drop_nulls()
        finite = finite.filter(finite.is_finite())
        if len(finite) >= MIN_VALUES and finite.n_unique() > 1 and not code_like(name):
            columns.append(name)
    return isolation_lazy(frame.lazy(), rows=frame.height, columns=columns, excluded_indices=indices, include_examples=include_examples)


def detect(frame: pl.DataFrame, *, ml: bool = False, progress: ProgressCallback | None = None, include_examples: bool = True) -> tuple[tuple[DetectorResult, ...], tuple[Finding, ...]]:
    results = []
    notify(progress, "Analizando columnas", 0, frame.width)
    for index, (name, dtype) in enumerate(frame.schema.items()):
        if dtype.is_integer() or dtype.is_float():
            results.append(numeric_outliers(frame[name], include_examples=include_examples))
        elif dtype == pl.String:
            results.append(broken_type(frame[name], include_examples=include_examples))
        notify(progress, "Analizando columnas", index + 1, frame.width)
    if ml:
        notify(progress, "Ejecutando ML")
    results.append(isolation_forest(frame, enabled=ml, include_examples=include_examples))
    return tuple(results), findings_for(results)


def findings_for(results) -> tuple[Finding, ...]:
    findings = []
    for result in results:
        indices = dict(result.parameters).get("contadores de fila omitidos")
        if indices:
            findings.append(Finding("possible_row_index", "info",
                                    f"Posible contador de fila omitido de ML: {indices}.",
                                    None))
        if result.count:
            code = {"IQR": "numeric_outliers", "tipo textual": "broken_type",
                    "IsolationForest": "isolation_outliers"}[result.method]
            if code == "numeric_outliers":
                detail = "valor fuera del rango habitual" if result.count == 1 else "valores fuera del rango habitual"
                message = (f"{result.count:,} de {result.evaluated_rows:,} filas con {detail} (IQR). "
                           "Revisá si los valores son esperados.")
            else:
                message = (f"{result.count:,} indicios por {result.method} "
                           f"sobre {result.evaluated_rows:,} filas evaluadas; revisar contexto.")
            findings.append(Finding(
                code, "warning", message,
                result.columns[0] if len(result.columns) == 1 else None, result.examples,
            ))
    return tuple(findings)
