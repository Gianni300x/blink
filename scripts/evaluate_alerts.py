"""Evaluación local reproducible sobre CSV reales etiquetados; no descarga ni entrena con etiquetas.

Ejemplo: python scripts/evaluate_alerts.py /ruta/shuttle.csv --target class --normal 1
La etiqueta define normalidad para este benchmark, no errores de calidad universales.
"""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import polars as pl

from blink_data_lint.core.ml import predict_sample, ML_ROWS, SEED
from blink_data_lint.core.analyzer import analyze
from blink_data_lint.core.execution import auxiliary, collect
from blink_data_lint.io.loader import load_dataset


def _sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def evaluate(path: Path, *, target: str, normal: str) -> dict:
    loaded = load_dataset(path)
    try:
        schema = loaded.scan.collect_schema()
        if target not in schema:
            raise ValueError("La columna objetivo no existe.")
        # La etiqueta jamás participa de selección de columnas ni ajuste del modelo.
        features = loaded.scan.drop(target)
        report = analyze(features, ml=True)
        ml = report.detectors[-1]
        if ml.reason:
            raise ValueError(f"ML omitido: {ml.reason}")
        if collect(loaded.scan.select(pl.col(target).is_null().any())).item():
            raise ValueError("Las etiquetas no pueden contener nulos.")
        row = auxiliary(schema, "__blink_row")
        key = auxiliary([*schema, row], "__blink_key")
        selected = loaded.scan.with_row_index(row, offset=1).filter(
            pl.all_horizontal(pl.col(n).is_finite().fill_null(False) for n in ml.columns))
        if ml.eligible_rows > ML_ROWS:
            selected = selected.with_columns(pl.col(row).hash(seed=SEED).alias(key)).sort([key, row]).head(ML_ROWS)
        sample = collect(selected.select(row, target, *ml.columns))
        predictions = predict_sample(sample.select(ml.columns))
        truth = sample[target].cast(pl.String) != normal
        positive = pl.Series(predictions == -1)
        tp = (truth & positive).sum()
        fp = (~truth & positive).sum()
        fn = (truth & ~positive).sum()
        tn = (~truth & ~positive).sum()
        assert tp + fp == ml.count
        assert sample.height == ml.evaluated_rows
        assert [e.row for e in ml.examples] == sample.filter(positive)[row].head(3).to_list()
        return {
            "source": str(path), "sha256": _sha256(path),
            "protocol": "Diagnóstico transductivo sin etiquetas; misma muestra y modelo de la CLI. Sin ajuste de umbrales con estas etiquetas.",
            "positive_definition": f"{target} != {normal}", "target_excluded_from_features": True,
            "rows": report.rows, "evaluated_rows": sample.height, "eligible_rows": ml.eligible_rows,
            "labels_in_sample": dict(Counter(sample[target].cast(pl.String).to_list())),
            "columns": ml.columns, "parameters": dict(ml.parameters),
            "true_positive": tp, "false_positive": fp, "false_negative": fn, "true_negative": tn,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
            "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
            "majority_baseline_accuracy": (fp + tn) / sample.height,
            "warnings_by_rule": dict(Counter(f.code for f in report.findings if f.severity == "warning")),
            "limitation": "Clases minoritarias no equivalen a datos erróneos. No mide generalización train/test ni valida reglas de negocio.",
        }
    finally:
        loaded.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    parser.add_argument("--target", required=True)
    parser.add_argument("--normal", required=True)
    args = parser.parse_args()
    print(json.dumps(evaluate(args.file, target=args.target, normal=args.normal), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
