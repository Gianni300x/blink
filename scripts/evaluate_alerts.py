"""Evaluación local reproducible sobre CSV reales etiquetados; no descarga ni entrena con etiquetas.

Ejemplo: python scripts/evaluate_alerts.py /ruta/shuttle.csv --target class --normal 1
La etiqueta define normalidad para este benchmark, no errores de calidad universales.
"""

import argparse
from collections import Counter
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import polars as pl

from blink_data_lint.core.ml import predict_sample, ML_ROWS, SEED
from blink_data_lint.core import ml as ml_module
from blink_data_lint.core.analyzer import analyze
from blink_data_lint.core.execution import auxiliary, collect
from blink_data_lint.io.loader import load_dataset


def _sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def evaluate(path: Path, *, target: str, normal: str) -> dict:
    digest_before = _sha256(path)
    loaded = load_dataset(path)
    try:
        schema = loaded.scan.collect_schema()
        if target not in schema:
            raise ValueError("La columna objetivo no existe.")
        count_name = auxiliary([target], "__blink_count")
        label_stats = collect(loaded.scan.select(pl.col(target).cast(pl.String)).group_by(target)
                              .agg(pl.len().alias(count_name)))
        if label_stats[target].null_count() or (label_stats[target].str.strip_chars() == "").any():
            raise ValueError("Las etiquetas no pueden contener nulos ni textos vacíos.")
        all_labels = dict(zip(label_stats[target].to_list(), label_stats[count_name].to_list()))
        if normal not in all_labels:
            raise ValueError("La etiqueta normal no existe en el archivo; revisá el protocolo.")
        # La etiqueta jamás participa de selección de columnas ni ajuste del modelo.
        features = loaded.scan.drop(target)
        report = analyze(features, ml=True)
        ml = report.detectors[-1]
        if ml.reason:
            raise ValueError(f"ML omitido: {ml.reason}")
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
        labels_in_sample = dict(Counter(sample[target].cast(pl.String).to_list()))
        if normal not in labels_in_sample:
            raise ValueError("La muestra no contiene registros de la clase normal; no se puede medir su tasa de falsos positivos.")
        if (tp + fp != ml.count or sample.height != ml.evaluated_rows
                or [e.row for e in ml.examples] != sample.filter(positive)[row].head(3).to_list()):
            raise ValueError("La evaluación no coincide con el informe de producción.")
        digest_after = _sha256(path)
        if digest_after != digest_before:
            raise ValueError("El archivo cambió durante la evaluación; resultado descartado.")
        precision = tp / (tp + fp) if tp + fp else None
        prevalence = (tp + fn) / sample.height
        return {
            "source": str(path), "sha256": digest_before, "source_unchanged": True,
            "python": sys.version.split()[0], "model_source_sha256": _sha256(Path(ml_module.__file__)),
            "versions": {name: importlib.metadata.version(name) for name in ("polars", "numpy", "scikit-learn")},
            "protocol": "Diagnóstico transductivo sin etiquetas; misma muestra y modelo de la CLI. Sin ajuste de umbrales con estas etiquetas.",
            "positive_definition": f"{target} != {normal}", "target_excluded_from_features": True,
            "rows": report.rows, "evaluated_rows": sample.height, "eligible_rows": ml.eligible_rows,
            "labels_in_file": all_labels, "labels_in_sample": labels_in_sample,
            "excluded_or_not_sampled_by_label": {k: v - labels_in_sample.get(k, 0) for k, v in all_labels.items()},
            "sample_rows_sha256": hashlib.sha256(json.dumps(sample[row].to_list()).encode()).hexdigest(),
            "predictions_sha256": hashlib.sha256(json.dumps(predictions.tolist()).encode()).hexdigest(),
            "columns": ml.columns, "parameters": dict(ml.parameters),
            "true_positive": tp, "false_positive": fp, "false_negative": fn, "true_negative": tn,
            "precision": precision,
            "positive_prevalence": prevalence,
            "precision_lift_over_random": precision / prevalence if precision is not None and prevalence else None,
            "false_positive_rate": fp / (fp + tn) if fp + tn else None,
            "alert_rate": (tp + fp) / sample.height,
            "recall": tp / (tp + fn) if tp + fn else None,
            "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
            "always_normal_accuracy": (fp + tn) / sample.height,
            "majority_baseline_accuracy": max(fp + tn, tp + fn) / sample.height,
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
