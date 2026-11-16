"""IsolationForest: selección acotada antes de materializar y evidencia original."""

from dataclasses import replace

import polars as pl

from blink_data_lint.core.models import DetectorResult
from blink_data_lint.core.execution import collect, auxiliary
from blink_data_lint.core.evidence import frame_examples

ML_ROWS = 10_000
ML_COLUMNS = 32
SEED = 42


def score_sample(sample: pl.DataFrame, base: dict, *, rows: list[int], sampling: str = "top-k hash de registro seed=42") -> DetectorResult:
    predictions = predict_sample(sample)
    if predictions is None:
        return DetectorResult(**base, reason="Sin variación representable en la muestra float32.")
    count = int((predictions == -1).sum())
    base.update(evaluated_rows=sample.height, count=count)
    examples = frame_examples(sample, (predictions == -1).nonzero()[0].tolist(), rows)
    return DetectorResult(**base, examples=examples, parameters=(
        ("muestreo", sampling), ("seed", str(SEED)), ("árboles", "64"), ("max_samples", str(min(256, sample.height))),
        ("límite de filas", str(ML_ROWS)), ("límite de columnas", str(ML_COLUMNS)),
        ("contamination", "auto"), ("umbral decision_function", "<0"), ("n_jobs", "1"),
    ))


def predict_sample(sample: pl.DataFrame):
    # Solo la muestra acotada cruza a NumPy. sklearn no se importa al arrancar.
    import numpy as np
    from sklearn.ensemble import IsolationForest

    matrix = sample.to_numpy().astype(np.float64)
    # sklearn convierte internamente a float32; escalar evita overflow.
    matrix /= np.maximum(np.abs(matrix).max(axis=0), 1)
    if not np.any(np.ptp(matrix.astype(np.float32), axis=0) > 0):
        return None
    model = IsolationForest(n_estimators=64, max_samples=min(256, sample.height), contamination="auto",
                            random_state=SEED, n_jobs=1)
    return model.fit_predict(matrix)


def isolation_lazy(source: pl.LazyFrame, *, rows: int, columns: list[str], excluded_indices=()) -> DetectorResult:
    schema = source.collect_schema()
    ml_columns = columns
    base = dict(method="IsolationForest", columns=(), total_rows=rows,
                eligible_rows=0, evaluated_rows=0, count=None)
    if len(ml_columns) > ML_COLUMNS:
        result = DetectorResult(**base, reason="Más de 32 columnas elegibles; trabajo ML acotado.")
    elif not ml_columns:
        result = DetectorResult(**base, reason="Sin columnas numéricas variables elegibles.")
    else:
        base["columns"] = tuple(ml_columns)
        row_name = auxiliary(schema, "__blink_row")
        key_name = auxiliary([*schema, row_name], "__blink_key")
        eligible = source.with_row_index(row_name, offset=1).select(row_name, *ml_columns).filter(
            pl.all_horizontal(pl.col(n).is_finite().fill_null(False) for n in ml_columns))
        base["eligible_rows"] = collect(eligible.select(pl.len())).item()
        if base["eligible_rows"] < 32:
            result = DetectorResult(**base, reason="Menos de 32 filas completas y finitas.")
        else:
            if base["eligible_rows"] > ML_ROWS:
                # Top-k por hash de la posición: reproducible, no favorece el inicio
                # del archivo y conserva solamente k filas para el ajuste del modelo.
                eligible = eligible.with_columns(pl.col(row_name).hash(seed=SEED).alias(key_name))
                eligible = eligible.sort([key_name, row_name]).head(ML_ROWS).drop(key_name)
            sample = collect(eligible)
            result = score_sample(sample.select(ml_columns), base, rows=sample[row_name].to_list(),
                                  sampling="top-k hash de registro seed=42")
    if excluded_indices:
        detail = ", ".join(excluded_indices)
        result = replace(result, parameters=(*result.parameters, ("contadores de fila omitidos", detail)))
        if result.reason:
            result = replace(result, reason=f"{result.reason} Contadores de fila omitidos de ML: {detail}.")
    return result
