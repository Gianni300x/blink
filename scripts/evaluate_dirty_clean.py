"""Evalúa por fila pares reales dirty/clean de Raha, sin entrenar con la referencia.

Uso interno: python scripts/evaluate_dirty_clean.py /ruta/raha --output informe.json
El directorio debe contener beers/{dirty,clean}.csv y rayyan/{dirty,clean}.csv.
Los archivos se verifican por hash. No se descargan ni redistribuyen datos.
"""

import argparse
from collections import Counter
import csv
import hashlib
import html
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import polars as pl

from blink_data_lint.core.analyzer import analyze
from blink_data_lint.core.detectors import numeric_outliers, broken_type
from blink_data_lint.core.ml import predict_sample, ML_ROWS, SEED
from blink_data_lint.core.execution import auxiliary, collect
from blink_data_lint.io.loader import load_dataset

SOURCES = {
    "beers": {"dirty": "7110bf4931a9445a1675e544d6c996817c739136239f8a2b02e088c7ec0a1f68",
              "clean": "373227df59ad197e154dd5149125789e415019535c7223355e9486ee1b3b93de"},
    "rayyan": {"dirty": "7e25e6db262b0c72ca2d9735d5959599cf5a582e1c705459507c7b45d0d1d174",
               "clean": "23159f43c0706782388ed8957ad0c74eb7b88bc98f34d65bd49296e186d4673f"},
}
ALIASES = {"beer_name": "beer-name", "brewery_name": "brewery-name"}


def digest(path):
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def normalize(value):
    # Política publicada por Raha Dataset.value_normalizer; solo para ground truth.
    return re.sub(r"[\t\n ]+", " ", html.unescape(value)).strip("\t\n ")


def read_strings(path):
    with Path(path).open(encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    if not rows or len(set(rows[0])) != len(rows[0]):
        raise ValueError("Cabecera ausente o duplicada.")
    if any(len(row) != len(rows[0]) for row in rows[1:]):
        raise ValueError("Filas inconsistentes en la referencia.")
    return rows[0], rows[1:]


def ground_truth(dirty, clean, *, keys, aliases=None):
    dh, dr = read_strings(dirty)
    ch, cr = read_strings(clean)
    aliases = aliases or {}
    if [aliases.get(n, n) for n in dh] != ch or len(dr) != len(cr):
        raise ValueError("Los esquemas o las cantidades de filas no coinciden.")
    positions = [dh.index(k) for k in keys]
    dk = [tuple(row[i] for i in positions) for row in dr]
    ck = [tuple(row[i] for i in positions) for row in cr]
    if dk != ck or len(set(dk)) != len(dk):
        raise ValueError("Claves/orden de filas incompatibles; no emparejar implícitamente.")
    changed = {(i, j) for i, (left, right) in enumerate(zip(dr, cr))
               for j, (a, b) in enumerate(zip(left, right)) if normalize(a) != normalize(b)}
    strict = sum(a != b for left, right in zip(dr, cr) for a, b in zip(left, right))
    return dh, dr, changed, strict


def metrics(predicted, truth, scope):
    predicted, truth, scope = set(predicted), set(truth), set(scope)
    if not predicted <= scope:
        raise ValueError("Predicciones fuera de cobertura.")
    target = truth & scope
    tp, fp = len(predicted & target), len(predicted - target)
    fn, tn = len(target - predicted), len(scope - predicted - target)
    return dict(evaluated_rows=len(scope), positive_rows=len(target), predicted_rows=len(predicted),
                true_positive=tp, false_positive=fp, false_negative=fn, true_negative=tn,
                precision=tp/(tp+fp) if tp+fp else None, recall=tp/(tp+fn) if tp+fn else None,
                positive_rows_outside_scope=len(truth-scope))


def rule_rows(frame, report):
    """Reglas localizables: faltantes, IQR y tipo roto; no agregados/IDs/drift.

    Se reconstruye la máscara exacta del detector 0.2 para evaluar todas las filas;
    se valida conteo y ejemplos contra producción. No se usan cotas redondeadas.
    """
    rows = set()
    checks = []
    for name, dtype in frame.schema.items():
        series = frame[name]
        missing = series.is_null()
        if dtype.is_float():
            missing |= series.is_nan()
        rows.update(missing.arg_true().to_list())
        if dtype.is_integer() or dtype.is_float():
            result = numeric_outliers(series)
            if result.reason:
                continue
            finite = series.drop_nulls().filter(series.drop_nulls().is_finite())
            scale = max(abs(finite.min()), abs(finite.max())) or 1
            values = finite.cast(pl.Float64) / scale
            q1 = values.quantile(.25, interpolation="linear")
            q3 = values.quantile(.75, interpolation="linear")
            original = series.cast(pl.Float64) / scale
            mask = series.is_finite().fill_null(False) & ((original < q1-1.5*(q3-q1)) | (original > q3+1.5*(q3-q1)))
        elif dtype == pl.String:
            result = broken_type(series)
            if result.reason:
                continue
            original = series.str.strip_chars()
            present = original.is_not_null() & (original != "")
            if dict(result.parameters)["tipo candidato"] == "numérico finito":
                valid = original.cast(pl.Float64, strict=False).is_finite().fill_null(False)
            else:
                valid = (original.str.contains(r"^\d{4}-\d{2}-\d{2}$") &
                         original.str.to_date(format="%Y-%m-%d", strict=False).is_not_null()).fill_null(False)
            mask = (present & ~valid).fill_null(False)
        else:
            continue
        found = mask.arg_true().to_list()
        if len(found) != result.count or [i+1 for i in found[:3]] != [e.row for e in result.examples]:
            raise ValueError("La reconstrucción de reglas no coincide con producción.")
        rows.update(found)
        checks.append(dict(column=name, method=result.method, count=len(found)))
    return rows, checks


def ml_rows(frame, ml):
    """Reconstruye las posiciones exactas de producción, sin etiquetas de referencia."""
    predicted = set()
    scope = set()
    if not ml.reason:
        row = auxiliary(frame.columns, "__blink_row")
        key = auxiliary([*frame.columns, row], "__blink_key")
        query = frame.lazy().with_row_index(row).select(row, *ml.columns).filter(
            pl.all_horizontal(pl.col(n).is_finite().fill_null(False) for n in ml.columns))
        if ml.eligible_rows > ML_ROWS:
            # Producción hashea registros desde 1, aunque el índice de evaluación sea 0-based.
            query = query.with_columns((pl.col(row)+1).hash(seed=SEED).alias(key)).sort([key,row]).head(ML_ROWS)
        sample = collect(query.select(row, *ml.columns))
        predictions = predict_sample(sample.select(ml.columns))
        selected = sample[row].to_list()
        scope = set(selected)
        predicted = {i for i, label in zip(selected, predictions.tolist()) if label == -1}
        if len(predicted) != ml.count or len(scope) != ml.evaluated_rows:
            raise ValueError("ML no coincide con el informe de producción.")
        if [i+1 for i, label in zip(selected, predictions.tolist()) if label == -1][:3] != [e.row for e in ml.examples]:
            raise ValueError("Posiciones ML incompatibles.")
    return predicted, scope


def evaluate_pair(dirty, clean, *, keys, aliases=None):
    before = {str(p): digest(p) for p in (dirty, clean)}
    names, raw, changed, strict = ground_truth(dirty, clean, keys=keys, aliases=aliases)
    # Solo el dirty entra a los detectores; clean es exclusivamente la referencia.
    loaded = load_dataset(dirty)
    try:
        frame = loaded.frame
        report = analyze(frame, ml=True)
    finally:
        loaded.close()
    if frame.shape != (len(raw), len(names)):
        raise ValueError("El parser no conserva la alineación de registros.")
    truth = {i for i, j in changed}
    rules, checks = rule_rows(frame, report)
    ml = report.detectors[-1]
    predicted, scope = ml_rows(frame, ml)
    ml_positions = {names.index(n) for n in ml.columns}
    relevant = {i for i, j in changed if j in ml_positions}
    combined = rules | predicted
    if any(digest(Path(p)) != sha for p, sha in before.items()):
        raise ValueError("Las fuentes cambiaron durante la evaluación.")
    source = Path(__file__).resolve().parents[1] / "src" / "blink_data_lint"
    return dict(rows=frame.height, columns=frame.width, sources=before, source_unchanged=True,
                ground_truth_policy="Normalización Raha: HTML unescape, colapsar tab/newline/espacios y strip; alineación explícita por claves y cabecera.",
                error_cells=len(changed), error_cells_strict=strict, error_rows=len(truth),
                error_cells_by_column=dict(Counter(names[j] for i,j in changed)),
                ml_columns=ml.columns, ml_reason=ml.reason, ml_parameters=dict(ml.parameters),
                ml_on_any_error_row=metrics(predicted, truth, scope),
                ml_on_errors_in_selected_columns=metrics(predicted, relevant, scope),
                rules_on_any_error_row=metrics(rules, truth, range(frame.height)),
                combined_on_any_error_row=metrics(combined, truth, range(frame.height)),
                ml_added_true_rows=len((predicted-rules)&truth), ml_added_false_rows=len((predicted-rules)-truth),
                model_inputs="Solo dirty; sin clean, etiquetas ni claves añadidas como features.", rule_checks=checks,
                source_code_sha256={str(p.relative_to(source)):digest(p) for p in source.rglob("*.py")})


def run(directory):
    results = {}
    for name in SOURCES:
        dirty, clean = directory/name/'dirty.csv', directory/name/'clean.csv'
        for kind, path in (("dirty",dirty),("clean",clean)):
            if digest(path) != SOURCES[name][kind]:
                raise ValueError(f"Fuente no fijada: {name}/{kind}")
        first = evaluate_pair(dirty, clean, keys=["index","id"] if name=='beers' else ["id"],
                              aliases=ALIASES if name=='beers' else None)
        second = evaluate_pair(dirty, clean, keys=["index","id"] if name=='beers' else ["id"],
                               aliases=ALIASES if name=='beers' else None)
        if first != second:
            raise ValueError("Resultado no reproducible.")
        results[name] = first
    return dict(unit="Filas, no celdas: una alerta ML no localiza por sí misma la celda errónea.",
                provenance="Pares originales Beers y Rayyan de BigDaMa/raha; sin inyectar errores.",
                results=results)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    report=run(args.directory)
    args.output.write_text(json.dumps(report,indent=2,allow_nan=False),encoding='utf-8')
    for name,result in report['results'].items():
        print(name, json.dumps({k:result[k] for k in ('error_cells','error_rows','ml_columns','ml_reason','ml_on_any_error_row','ml_on_errors_in_selected_columns','ml_added_true_rows','ml_added_false_rows')},ensure_ascii=True))


if __name__=='__main__':
    main()
